from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.warp import transform_bounds
from rasterio.windows import from_bounds
from sklearn.decomposition import PCA
from sklearn.ensemble import IsolationForest

from app.pipeline.raster_baseline import _heatmap_geojson
from app.providers.stac import Scene


@dataclass(frozen=True)
class AdvancedSensorResult:
    sensor: str
    summary: dict[str, Any]
    heatmap: dict[str, Any]
    score: np.ndarray
    provenance: dict[str, Any]


def _read_multiband_bbox(
    href: str,
    bbox_wgs84: tuple[float, float, float, float],
    max_bands: int = 32,
    shape: tuple[int, int] = (192, 192),
) -> tuple[np.ndarray, list[int]]:
    with rasterio.Env(GDAL_HTTP_MULTIRANGE="YES", GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR"):
        with rasterio.open(href) as src:
            bounds = transform_bounds("EPSG:4326", src.crs, *bbox_wgs84, densify_pts=21)
            window = from_bounds(*bounds, transform=src.transform)
            count = src.count
            if count < 2:
                raise ValueError("Hyperspectral asset is not multiband.")
            band_indexes = np.linspace(1, count, min(max_bands, count), dtype=int)
            band_indexes = sorted(set(int(v) for v in band_indexes))
            data = src.read(
                band_indexes,
                window=window,
                out_shape=(len(band_indexes), *shape),
                resampling=Resampling.bilinear,
                masked=True,
            )
    return data.filled(np.nan).astype(np.float32), band_indexes


def _pick_hyperspectral_asset(assets: dict[str, Any]) -> tuple[str, str]:
    candidates: list[tuple[int, str, str]] = []
    for key, asset in assets.items():
        if not isinstance(asset, dict):
            continue
        href = asset.get("href")
        if not href:
            continue
        media_type = str(asset.get("type", "")).lower()
        title = str(asset.get("title", "")).lower()
        roles = " ".join(str(v).lower() for v in asset.get("roles", []))
        score = 0
        if "tiff" in media_type or str(href).lower().endswith((".tif", ".tiff")):
            score += 3
        if "spectral" in title or "hyperspectral" in title or "reflectance" in title:
            score += 3
        if "data" in roles:
            score += 1
        candidates.append((score, key, str(href)))
    if not candidates:
        raise KeyError("No readable hyperspectral raster asset found.")
    candidates.sort(reverse=True)
    _, key, href = candidates[0]
    return key, href


def _rank_anomaly(features: np.ndarray, valid: np.ndarray) -> np.ndarray:
    out = np.full(valid.shape, np.nan, dtype=np.float32)
    samples = features[valid]
    if samples.shape[0] < 256:
        raise ValueError("Insufficient valid hyperspectral pixels.")
    model = IsolationForest(
        n_estimators=260,
        contamination="auto",
        random_state=42,
        n_jobs=-1,
    )
    model.fit(samples)
    raw = -model.score_samples(samples)
    low, high = np.quantile(raw, [0.01, 0.99])
    out[valid] = np.clip((raw - low) / max(high - low, 1e-6), 0.0, 1.0)
    return out


def analyze_hyperspectral_scene(
    scene: Scene,
    bbox: tuple[float, float, float, float],
) -> AdvancedSensorResult:
    asset_key, href = _pick_hyperspectral_asset(scene.assets)
    cube, band_indexes = _read_multiband_bbox(href, bbox)

    height, width = cube.shape[1:]
    pixels = np.moveaxis(cube, 0, -1)
    valid = np.all(np.isfinite(pixels), axis=-1)
    flat = pixels[valid]
    if flat.shape[0] < 256:
        raise ValueError("Insufficient valid hyperspectral pixels.")

    # Per-band robust normalization before representation learning.
    med = np.nanmedian(flat, axis=0)
    q25 = np.nanquantile(flat, 0.25, axis=0)
    q75 = np.nanquantile(flat, 0.75, axis=0)
    scale = np.maximum(q75 - q25, 1e-6)
    normalized = (flat - med) / scale

    n_components = min(12, normalized.shape[1], max(2, normalized.shape[0] - 1))
    pca = PCA(n_components=n_components, random_state=42)
    embedding = pca.fit_transform(normalized)

    feature_grid = np.full((height, width, n_components), np.nan, dtype=np.float32)
    feature_grid[valid] = embedding.astype(np.float32)
    score = _rank_anomaly(feature_grid, valid)

    values = score[np.isfinite(score)]
    summary = {
        "status": "model_inference",
        "score_type": "hyperspectral_unsupervised_anomaly_rank",
        "not_probability": True,
        "valid_pixels": int(values.size),
        "source_band_count": int(cube.shape[0]),
        "pca_components": int(n_components),
        "explained_variance": float(np.sum(pca.explained_variance_ratio_)),
        "p90": float(np.quantile(values, 0.90)),
        "p99": float(np.quantile(values, 0.99)),
    }

    return AdvancedSensorResult(
        sensor="hyperspectral",
        summary=summary,
        heatmap=_heatmap_geojson(score, bbox),
        score=score,
        provenance={
            "provider": scene.provider,
            "collection": scene.collection,
            "item_id": scene.item_id,
            "asset_key": asset_key,
            "sampled_band_indexes": band_indexes,
            "method": "robust_normalization+pca+isolation_forest",
            "note": "Hyperspectral anomaly rank; no material identity is asserted without spectral-library validation.",
        },
    )


def _find_temperature_asset(scene: Scene) -> tuple[str, dict[str, Any]]:
    ranked: list[tuple[int, str, dict[str, Any]]] = []
    for key, asset in scene.assets.items():
        if not isinstance(asset, dict) or not asset.get("href"):
            continue
        text = f"{key} {asset.get('title','')} {' '.join(asset.get('roles', []))}".lower()
        score = 0
        if "surface temperature" in text:
            score += 10
        if key.lower() in {"lwir11", "st", "st_b10", "temperature"}:
            score += 6
        if "temperature" in text or "thermal" in text:
            score += 3
        if score:
            ranked.append((score, key, asset))
    if not ranked:
        raise KeyError("No surface-temperature asset found in thermal scene.")
    ranked.sort(key=lambda item: item[0], reverse=True)
    _, key, asset = ranked[0]
    return key, asset


def _read_single_band(
    href: str,
    bbox_wgs84: tuple[float, float, float, float],
    shape: tuple[int, int] = (192, 192),
) -> np.ndarray:
    with rasterio.Env(GDAL_HTTP_MULTIRANGE="YES", GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR"):
        with rasterio.open(href) as src:
            bounds = transform_bounds("EPSG:4326", src.crs, *bbox_wgs84, densify_pts=21)
            window = from_bounds(*bounds, transform=src.transform)
            arr = src.read(
                1,
                window=window,
                out_shape=shape,
                resampling=Resampling.bilinear,
                masked=True,
            )
    return arr.filled(np.nan).astype(np.float32)


def _asset_scale_offset(asset: dict[str, Any]) -> tuple[float, float]:
    bands = asset.get("raster:bands") or asset.get("bands") or []
    if bands and isinstance(bands[0], dict):
        scale = bands[0].get("scale")
        offset = bands[0].get("offset")
        return float(scale or 1.0), float(offset or 0.0)
    return 1.0, 0.0


def analyze_thermal_stack(
    scenes: list[Scene],
    bbox: tuple[float, float, float, float],
) -> AdvancedSensorResult:
    observations: list[np.ndarray] = []
    used: list[dict[str, Any]] = []

    for scene in scenes:
        try:
            key, asset = _find_temperature_asset(scene)
            arr = _read_single_band(str(asset["href"]), bbox)
            scale, offset = _asset_scale_offset(asset)
            arr = arr * scale + offset
        except (KeyError, OSError, ValueError):
            continue
        if np.isfinite(arr).sum() < 256:
            continue
        observations.append(arr)
        used.append(
            {
                "provider": scene.provider,
                "collection": scene.collection,
                "item_id": scene.item_id,
                "datetime": scene.datetime,
                "asset_key": key,
            }
        )

    if not observations:
        raise ValueError("No usable surface-temperature observations.")

    cube = np.stack(observations)
    median_temp = np.nanmedian(cube, axis=0)
    temporal_std = np.nanstd(cube, axis=0)
    spatial_y, spatial_x = np.gradient(median_temp)
    spatial_gradient = np.hypot(spatial_x, spatial_y)

    features = np.stack([median_temp, temporal_std, spatial_gradient], axis=-1)
    valid = np.all(np.isfinite(features), axis=-1)
    score = _rank_anomaly(features, valid)
    values = score[np.isfinite(score)]

    return AdvancedSensorResult(
        sensor="thermal_surface_temperature",
        summary={
            "status": "model_inference",
            "score_type": "thermal_persistence_anomaly_rank",
            "not_probability": True,
            "observations_used": len(used),
            "valid_pixels": int(values.size),
            "p90": float(np.quantile(values, 0.90)),
            "p99": float(np.quantile(values, 0.99)),
            "thermal_inertia_claim": False,
        },
        heatmap=_heatmap_geojson(score, bbox),
        score=score,
        provenance={
            "scenes": used,
            "features": ["median_surface_temperature", "temporal_std", "spatial_temperature_gradient"],
            "note": "This is thermal persistence, not day/night thermal inertia. True inertia requires paired diurnal observations.",
        },
    )
