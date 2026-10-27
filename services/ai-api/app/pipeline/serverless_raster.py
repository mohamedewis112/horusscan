from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from typing import Any

import httpx
import numpy as np
from sklearn.ensemble import IsolationForest
from tifffile import imread

from app.pipeline.sentinel2 import (
    compute_spectral_features,
    spectral_anomaly_score,
    summarize_score,
)


PC_STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
PC_TILER = "https://planetarycomputer.microsoft.com/api/data/v1"


CALIBRATION_V1_WEIGHTS = {
    "spectral": 0.7902439726902944,
    "sar": 0.8741349766323481,
    "terrain": 1.0054733125045956,
    "thermal": 1.5,
    "olmoearth": 0.96207601682545,
}

CALIBRATION_V1_METADATA = {
    "dataset": "unesco-egypt-public-v1-2026-10-27",
    "cases": ["Abu Mena", "Site of Memphis"],
    "controls": "deterministic_unlabeled_controls",
    "formula": "clip(exp(1.5 * mean(site_rediscovery-control_rediscovery)), 0.65, 1.5)",
    "validation_cases_excluded_from_tuning": ["Temple of Karnak", "Temple of Luxor", "Ancient Thebes Necropolis"],
    "large_landscape_case_excluded": "Pyramid fields from Giza to Dahshur",
}



@dataclass(frozen=True)
class ServerlessScene:
    collection: str
    item_id: str
    datetime: str | None
    cloud_cover: float | None
    properties: dict[str, Any]
    assets: tuple[str, ...]
    asset_hrefs: dict[str, str]
    bbox: tuple[float, float, float, float] | None


def _heatmap_geojson(
    score: np.ndarray,
    bbox: tuple[float, float, float, float],
) -> dict[str, Any]:
    min_lon, min_lat, max_lon, max_lat = bbox
    height, width = score.shape
    # Keep analysis at full raster resolution, but expose a denser 48x48
    # visualization grid so the entire AOI is represented without sending
    # tens of thousands of polygons to the browser.
    stride = max(1, min(height, width) // 48)
    features: list[dict[str, Any]] = []

    for row in range(0, height, stride):
        for col in range(0, width, stride):
            block = score[row : row + stride, col : col + stride]
            valid = block[np.isfinite(block)]
            if valid.size == 0:
                continue
            value = float(np.mean(valid))
            lon = min_lon + ((col + stride / 2) / width) * (max_lon - min_lon)
            lat = max_lat - ((row + stride / 2) / height) * (max_lat - min_lat)
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [lon, lat]},
                    "properties": {"anomaly_score": value},
                }
            )

    return {"type": "FeatureCollection", "features": features}


def score_to_heatmap(
    score: np.ndarray,
    bbox: tuple[float, float, float, float],
) -> dict[str, Any]:
    """Public wrapper used after AOI masking."""
    return _heatmap_geojson(score, bbox)


async def search_pc_collection(
    collection: str,
    bbox: tuple[float, float, float, float],
    start: datetime | None = None,
    end: datetime | None = None,
    max_cloud: float | None = None,
    limit: int = 5,
) -> list[ServerlessScene]:
    payload: dict[str, Any] = {
        "collections": [collection],
        "bbox": list(bbox),
        "limit": limit,
    }
    if start is not None and end is not None:
        payload["datetime"] = f"{start.isoformat()}/{end.isoformat()}"
        payload["sortby"] = [{"field": "properties.datetime", "direction": "desc"}]
    if max_cloud is not None:
        payload["query"] = {"eo:cloud_cover": {"lte": max_cloud}}

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(f"{PC_STAC}/search", json=payload)
        response.raise_for_status()
        features = response.json().get("features", [])

    return [
        ServerlessScene(
            collection=str(item.get("collection") or collection),
            item_id=str(item["id"]),
            datetime=(
                item.get("properties", {}).get("datetime")
                or item.get("properties", {}).get("start_datetime")
            ),
            cloud_cover=item.get("properties", {}).get("eo:cloud_cover"),
            properties=item.get("properties", {}),
            assets=tuple(sorted(item.get("assets", {}).keys())),
            asset_hrefs={
                str(key): str(value.get("href"))
                for key, value in item.get("assets", {}).items()
                if isinstance(value, dict) and value.get("href")
            },
            bbox=(
                tuple(float(value) for value in item["bbox"])
                if item.get("bbox") and len(item["bbox"]) == 4
                else None
            ),
        )
        for item in features
    ]


async def search_pc_sentinel2(
    bbox: tuple[float, float, float, float],
    start: datetime,
    end: datetime,
    max_cloud: float = 25.0,
    limit: int = 5,
) -> list[ServerlessScene]:
    return await search_pc_collection(
        "sentinel-2-l2a",
        bbox,
        start,
        end,
        max_cloud=max_cloud,
        limit=limit,
    )


async def read_pc_item_assets_npy(
    scene: ServerlessScene,
    bbox: tuple[float, float, float, float],
    assets: tuple[str, ...],
    width: int = 192,
    height: int = 192,
    resampling: str = "bilinear",
    unscale: bool = False,
    return_mask: bool = True,
) -> tuple[np.ndarray, np.ndarray]:
    missing = [asset for asset in assets if asset not in scene.assets]
    if missing:
        raise KeyError(
            f"Scene {scene.item_id} missing assets {missing}; available={list(scene.assets)}"
        )

    min_lon, min_lat, max_lon, max_lat = bbox
    url = (
        f"{PC_TILER}/item/bbox/"
        f"{min_lon},{min_lat},{max_lon},{max_lat}/{width}x{height}.npy"
    )

    params: list[tuple[str, str]] = [
        ("collection", scene.collection),
        ("item", scene.item_id),
        ("asset_as_band", "true"),
        ("return_mask", "true" if return_mask else "false"),
        ("resampling", resampling),
        ("unscale", "true" if unscale else "false"),
    ]
    params.extend(("assets", asset) for asset in assets)

    async with httpx.AsyncClient(timeout=90.0) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        if "application/x-binary" not in response.headers.get("content-type", ""):
            raise RuntimeError(
                "Planetary Computer tiler did not return NumpyTile binary: "
                f"{response.headers.get('content-type')}"
            )

    array = np.load(BytesIO(response.content), allow_pickle=False)
    if array.ndim != 3 or array.shape[0] < len(assets):
        raise RuntimeError(f"Unexpected NumpyTile shape: {array.shape}")

    data = array[: len(assets)].astype(np.float32)
    if not return_mask:
        mask = np.all(np.isfinite(data), axis=0)
    elif array.shape[0] > len(assets):
        mask = array[-1] > 0
    else:
        mask = np.all(np.isfinite(data), axis=0)
    return data, mask



async def _sign_pc_href(href: str) -> str:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            "https://planetarycomputer.microsoft.com/api/sas/v1/sign",
            params={"href": href},
        )
        response.raise_for_status()
        payload = response.json()
    signed = payload.get("href")
    if not signed:
        raise RuntimeError("Planetary Computer SAS endpoint returned no signed href.")
    return str(signed)


def _crop_resample_from_item_bbox(
    array: np.ndarray,
    item_bbox: tuple[float, float, float, float],
    target_bbox: tuple[float, float, float, float],
    out_shape: tuple[int, int] = (192, 192),
) -> np.ndarray:
    west, south, east, north = item_bbox
    min_lon, min_lat, max_lon, max_lat = target_bbox
    height, width = array.shape

    if east <= west or north <= south:
        raise ValueError(f"Invalid source item bbox: {item_bbox}")

    col0 = int(np.floor((min_lon - west) / (east - west) * width))
    col1 = int(np.ceil((max_lon - west) / (east - west) * width))
    row0 = int(np.floor((north - max_lat) / (north - south) * height))
    row1 = int(np.ceil((north - min_lat) / (north - south) * height))

    col0 = max(0, min(width - 1, col0))
    col1 = max(col0 + 1, min(width, col1))
    row0 = max(0, min(height - 1, row0))
    row1 = max(row0 + 1, min(height, row1))

    crop = array[row0:row1, col0:col1]
    if crop.size == 0:
        raise ValueError(
            f"DEM crop is empty for item_bbox={item_bbox}, target_bbox={target_bbox}"
        )

    out_h, out_w = out_shape
    rows = np.linspace(0, crop.shape[0] - 1, out_h).round().astype(int)
    cols = np.linspace(0, crop.shape[1] - 1, out_w).round().astype(int)
    return crop[np.ix_(rows, cols)].astype(np.float32)


async def read_pc_dem_cog_direct(
    scene: ServerlessScene,
    bbox: tuple[float, float, float, float],
    asset: str = "data",
    out_shape: tuple[int, int] = (192, 192),
) -> np.ndarray:
    href = scene.asset_hrefs.get(asset)
    if not href:
        raise KeyError(
            f"Scene {scene.item_id} has no href for asset {asset}."
        )
    if scene.bbox is None:
        raise ValueError(f"Scene {scene.item_id} has no item bbox.")

    signed_href = await _sign_pc_href(href)
    async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
        response = await client.get(signed_href)
        response.raise_for_status()

    arr = np.asarray(imread(BytesIO(response.content)))
    arr = np.squeeze(arr)
    if arr.ndim == 3:
        if arr.shape[0] == 1:
            arr = arr[0]
        elif arr.shape[-1] == 1:
            arr = arr[..., 0]
        else:
            arr = arr[..., 0]
    if arr.ndim != 2:
        raise RuntimeError(f"Unexpected direct DEM COG shape: {arr.shape}")

    return _crop_resample_from_item_bbox(
        arr.astype(np.float32),
        scene.bbox,
        bbox,
        out_shape=out_shape,
    )


def _isolation_score(features: list[np.ndarray], valid: np.ndarray) -> np.ndarray:
    stack = np.stack(features, axis=-1).astype(np.float32)
    valid = valid & np.all(np.isfinite(stack), axis=-1)
    samples = stack[valid]
    if samples.shape[0] < 256:
        raise ValueError("Insufficient valid pixels for anomaly analysis.")

    model = IsolationForest(
        n_estimators=220,
        contamination="auto",
        random_state=42,
        n_jobs=-1,
    )
    model.fit(samples)
    raw_values = -model.score_samples(samples)

    low, high = np.quantile(raw_values, [0.01, 0.99])
    scaled = np.clip(
        (raw_values - low) / max(float(high - low), 1e-6),
        0.0,
        1.0,
    )

    output = np.full(valid.shape, np.nan, dtype=np.float32)
    output[valid] = scaled.astype(np.float32)
    return output


def _summary(
    score: np.ndarray,
    score_type: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    values = score[np.isfinite(score)]
    result: dict[str, Any] = {
        "status": "model_inference" if values.size else "insufficient_evidence",
        "valid_pixels": int(values.size),
        "score_type": score_type,
        "not_probability": True,
    }
    if values.size:
        result.update(
            p50=float(np.quantile(values, 0.50)),
            p90=float(np.quantile(values, 0.90)),
            p99=float(np.quantile(values, 0.99)),
        )
    if extra:
        result.update(extra)
    return result


async def analyze_sentinel2_serverless(
    scene: ServerlessScene,
    bbox: tuple[float, float, float, float],
) -> dict[str, Any]:
    cube, mask = await read_pc_item_assets_npy(
        scene,
        bbox,
        ("B02", "B04", "B08", "B11"),
    )
    blue, red, nir, swir1 = cube

    valid = (
        mask
        & np.isfinite(blue)
        & np.isfinite(red)
        & np.isfinite(nir)
        & np.isfinite(swir1)
        & (blue > 0)
        & (red > 0)
        & (nir > 0)
        & (swir1 > 0)
    )

    features = compute_spectral_features(
        red=red,
        nir=nir,
        swir1=swir1,
        blue=blue,
    )
    score = spectral_anomaly_score(features, valid)
    summary = summarize_score(score)
    summary.update(
        {
            "provider": "microsoft-planetary-computer-titiler",
            "collection": scene.collection,
            "scene_id": scene.item_id,
            "serverless_raster_path": True,
        }
    )
    return {
        "summary": summary,
        "heatmap": _heatmap_geojson(score, bbox),
        "score": score,
        "provenance": {
            "provider": "microsoft-planetary-computer",
            "collection": scene.collection,
            "item_id": scene.item_id,
            "datetime": scene.datetime,
            "assets": ["B02", "B04", "B08", "B11"],
        },
    }


async def analyze_sar_serverless(
    scenes: list[ServerlessScene],
    bbox: tuple[float, float, float, float],
) -> dict[str, Any]:
    usable = [scene for scene in scenes if {"vv", "vh"}.issubset(scene.assets)][:4]
    if not usable:
        raise ValueError("No Sentinel-1 RTC scene with both VV and VH assets.")

    reads = await asyncio.gather(
        *[
            read_pc_item_assets_npy(
                scene,
                bbox,
                ("vv", "vh"),
                width=192,
                height=192,
            )
            for scene in usable
        ],
        return_exceptions=True,
    )

    vv_list: list[np.ndarray] = []
    vh_list: list[np.ndarray] = []
    masks: list[np.ndarray] = []
    used: list[ServerlessScene] = []
    for scene, result in zip(usable, reads):
        if isinstance(result, Exception):
            continue
        cube, mask = result
        vv, vh = cube
        valid = mask & np.isfinite(vv) & np.isfinite(vh) & (vv > 0) & (vh > 0)
        if valid.sum() < 256:
            continue
        vv = np.where(valid, vv, np.nan)
        vh = np.where(valid, vh, np.nan)
        vv_list.append(vv)
        vh_list.append(vh)
        masks.append(valid)
        used.append(scene)

    if not vv_list:
        raise ValueError("Sentinel-1 RTC NumpyTile reads returned no usable VV/VH pixels.")

    vv_cube = np.stack(vv_list)
    vh_cube = np.stack(vh_list)
    eps = np.float32(1e-8)
    log_vv_cube = 10.0 * np.log10(np.maximum(vv_cube, eps))
    log_vh_cube = 10.0 * np.log10(np.maximum(vh_cube, eps))

    log_vv = np.nanmedian(log_vv_cube, axis=0)
    log_vh = np.nanmedian(log_vh_cube, axis=0)
    ratio = log_vv - log_vh
    temporal_var = np.nanvar(log_vv_cube, axis=0)
    gy, gx = np.gradient(log_vv)
    texture = np.hypot(gx, gy)

    valid = np.isfinite(log_vv) & np.isfinite(log_vh) & np.isfinite(temporal_var)
    score = _isolation_score([log_vv, log_vh, ratio, temporal_var, texture], valid)

    return {
        "summary": _summary(
            score,
            "sentinel1_rtc_temporal_anomaly_rank",
            {
                "observations_used": len(used),
                "temporal_stack": len(used) > 1,
                "serverless_raster_path": True,
            },
        ),
        "heatmap": _heatmap_geojson(score, bbox),
        "score": score,
        "provenance": {
            "provider": "microsoft-planetary-computer",
            "collection": "sentinel-1-rtc",
            "scenes": [scene.item_id for scene in used],
            "assets": ["vv", "vh"],
        },
    }


async def analyze_dem_serverless(
    scenes: list[ServerlessScene],
    bbox: tuple[float, float, float, float],
) -> dict[str, Any]:
    usable = [scene for scene in scenes if "data" in scene.assets][:4]
    if not usable:
        raise ValueError("No Copernicus DEM scene with data asset.")

    reads = await asyncio.gather(
        *[
            read_pc_dem_cog_direct(
                scene,
                bbox,
                "data",
                out_shape=(192, 192),
            )
            for scene in usable
        ],
        return_exceptions=True,
    )

    tiles: list[np.ndarray] = []
    used: list[ServerlessScene] = []
    errors: list[str] = []

    for scene, result in zip(usable, reads):
        if isinstance(result, Exception):
            errors.append(f"{scene.item_id}: {type(result).__name__}: {result}")
            continue
        raw = result
        dem_valid = (
            np.isfinite(raw)
            & (raw > -1000.0)
            & (raw < 10000.0)
        )
        elevation = np.where(dem_valid, raw, np.nan)
        if np.isfinite(elevation).sum() < 256:
            errors.append(
                f"{scene.item_id}: fewer than 256 finite elevation pixels"
            )
            continue
        tiles.append(elevation)
        used.append(scene)

    if not tiles:
        detail = "; ".join(errors[:3]) if errors else "no usable TIFF reads"
        raise ValueError(f"Copernicus DEM direct COG reads returned no usable pixels: {detail}")

    elevation = np.nanmedian(np.stack(tiles), axis=0)
    gy, gx = np.gradient(elevation)
    slope = np.hypot(gx, gy)
    gyy, _ = np.gradient(gy)
    _, gxx = np.gradient(gx)
    curvature = gxx + gyy
    ry, rx = np.gradient(slope)
    roughness = np.hypot(rx, ry)

    valid = (
        np.isfinite(elevation)
        & np.isfinite(slope)
        & np.isfinite(curvature)
        & np.isfinite(roughness)
    )
    score = _isolation_score([elevation, slope, curvature, roughness], valid)

    return {
        "summary": _summary(
            score,
            "terrain_morphology_anomaly_rank",
            {
                "tiles_used": len(used),
                "serverless_raster_path": True,
                "transport": "direct_signed_cog",
            },
        ),
        "heatmap": _heatmap_geojson(score, bbox),
        "score": score,
        "provenance": {
            "provider": "microsoft-planetary-computer",
            "collection": "cop-dem-glo-30",
            "scenes": [scene.item_id for scene in used],
            "asset": "data",
            "transport": "direct_signed_cog",
        },
    }

async def analyze_thermal_serverless(
    scenes: list[ServerlessScene],
    bbox: tuple[float, float, float, float],
) -> dict[str, Any]:
    usable: list[tuple[ServerlessScene, str]] = []
    for scene in scenes:
        if "lwir11" in scene.assets:
            usable.append((scene, "lwir11"))
        elif "lwir" in scene.assets:
            usable.append((scene, "lwir"))
        if len(usable) >= 5:
            break

    if not usable:
        raise ValueError("No Landsat L2 surface-temperature asset available.")

    reads = await asyncio.gather(
        *[
            read_pc_item_assets_npy(
                scene,
                bbox,
                (asset,),
                width=192,
                height=192,
                unscale=True,
            )
            for scene, asset in usable
        ],
        return_exceptions=True,
    )

    observations: list[np.ndarray] = []
    used: list[tuple[ServerlessScene, str]] = []
    for pair, result in zip(usable, reads):
        if isinstance(result, Exception):
            continue
        scene, asset = pair
        cube, mask = result
        temp = np.where(mask & np.isfinite(cube[0]) & (cube[0] > 0), cube[0], np.nan)
        if np.isfinite(temp).sum() < 256:
            continue
        observations.append(temp)
        used.append((scene, asset))

    if not observations:
        raise ValueError("Landsat temperature NumpyTile reads returned no usable pixels.")

    temp_cube = np.stack(observations)
    median_temp = np.nanmedian(temp_cube, axis=0)
    temporal_std = np.nanstd(temp_cube, axis=0)
    gy, gx = np.gradient(median_temp)
    spatial_gradient = np.hypot(gx, gy)
    valid = np.isfinite(median_temp) & np.isfinite(temporal_std)

    score = _isolation_score([median_temp, temporal_std, spatial_gradient], valid)
    return {
        "summary": _summary(
            score,
            "thermal_persistence_anomaly_rank",
            {
                "observations_used": len(used),
                "serverless_raster_path": True,
                "thermal_inertia_claim": False,
            },
        ),
        "heatmap": _heatmap_geojson(score, bbox),
        "score": score,
        "provenance": {
            "provider": "microsoft-planetary-computer",
            "collection": "landsat-c2-l2",
            "scenes": [
                {"item_id": scene.item_id, "asset": asset, "datetime": scene.datetime}
                for scene, asset in used
            ],
            "note": "Surface-temperature persistence; not day/night thermal inertia.",
        },
    }


def _profile_weight(name: str, profile: str) -> float:
    if profile != "calibration_v1":
        return 1.0
    key = "olmoearth" if name.startswith("olmoearth_") else name
    return float(CALIBRATION_V1_WEIGHTS.get(key, 1.0))


def fuse_serverless_layers(
    layers: dict[str, np.ndarray],
    bbox: tuple[float, float, float, float],
    profile: str = "equal_v0",
) -> dict[str, Any]:
    if not layers:
        raise ValueError("No evidence layers available for HyperFusion.")
    if profile not in {"equal_v0", "calibration_v1"}:
        raise ValueError(f"Unknown HyperFusion profile: {profile}")

    target_shape = next(iter(layers.values())).shape
    aligned = {
        name: score
        for name, score in layers.items()
        if score.shape == target_shape
    }
    if not aligned:
        raise ValueError("No shape-compatible evidence layers available.")

    names = list(aligned)
    cube = np.stack([aligned[name] for name in names], axis=0).astype(np.float32)
    valid = np.isfinite(cube)

    weights = np.asarray(
        [_profile_weight(name, profile) for name in names],
        dtype=np.float32,
    )[:, None, None]
    effective_weights = weights * valid
    weight_sum = np.sum(effective_weights, axis=0)
    valid_count = np.sum(valid, axis=0)

    clipped = np.clip(cube, 1e-4, 1.0)
    weighted_log = np.nansum(np.log(clipped) * effective_weights, axis=0)
    geometric = np.exp(
        np.divide(
            weighted_log,
            weight_sum,
            out=np.zeros_like(weighted_log, dtype=np.float32),
            where=weight_sum > 0,
        )
    )

    weighted_high = np.sum(
        ((cube >= 0.75) & valid) * weights,
        axis=0,
    )
    agreement = np.divide(
        weighted_high,
        weight_sum,
        out=np.zeros_like(geometric, dtype=np.float32),
        where=weight_sum > 0,
    )
    coverage = valid_count.astype(np.float32) / float(len(names))

    fused = np.where(
        weight_sum > 0,
        np.clip(
            0.65 * geometric
            + 0.25 * agreement
            + 0.10 * coverage * geometric,
            0.0,
            1.0,
        ),
        np.nan,
    ).astype(np.float32)

    strong_fraction = float(
        np.mean(agreement[np.isfinite(fused)] >= 0.66)
    ) if np.isfinite(fused).any() else 0.0

    if strong_fraction >= 0.25:
        evidence_agreement = "strong"
    elif strong_fraction >= 0.08:
        evidence_agreement = "moderate"
    else:
        evidence_agreement = "conflicting"

    reliability_weights = {
        name: _profile_weight(name, profile) for name in names
    }

    return {
        "summary": _summary(
            fused,
            "horus_hyperfusion_serverless_rank",
            {
                "sensors": names,
                "evidence_agreement": evidence_agreement,
                "agreement_fraction": strong_fraction,
                "mean_sensor_coverage": float(np.nanmean(coverage)),
                "calibrated_probability": False,
                "fusion_method": "reliability_weighted_geometric_evidence+agreement+coverage_gating",
                "fusion_profile": profile,
                "reliability_weights": reliability_weights,
                "calibration_metadata": (
                    CALIBRATION_V1_METADATA if profile == "calibration_v1" else None
                ),
            },
        ),
        "heatmap": _heatmap_geojson(fused, bbox),
        "score": fused,
        "provenance": {
            "inputs": names,
            "engine": "horus-hyperfusion-serverless-v1",
            "fusion_profile": profile,
            "reliability_weights": reliability_weights,
        },
    }

async def run_serverless_hyperfusion(
    bbox: tuple[float, float, float, float],
    start: datetime,
    end: datetime,
    max_cloud: float = 20.0,
    include_branch_heatmaps: bool = False,
    fusion_profile: str = "calibration_v1",
) -> dict[str, Any]:
    discovery = await asyncio.gather(
        search_pc_collection("sentinel-2-l2a", bbox, start, end, max_cloud, 5),
        search_pc_collection("sentinel-1-rtc", bbox, start, end, None, 6),
        search_pc_collection("cop-dem-glo-30", bbox, None, None, None, 4),
        search_pc_collection("landsat-c2-l2", bbox, start, end, max_cloud, 8),
        return_exceptions=True,
    )

    labels = ("sentinel2", "sentinel1", "dem", "thermal")
    discovery_errors = {
        label: f"{type(value).__name__}: {value}" if isinstance(value, Exception) else None
        for label, value in zip(labels, discovery)
    }

    s2_scenes = [] if isinstance(discovery[0], Exception) else discovery[0]
    s1_scenes = [] if isinstance(discovery[1], Exception) else discovery[1]
    dem_scenes = [] if isinstance(discovery[2], Exception) else discovery[2]
    thermal_scenes = [] if isinstance(discovery[3], Exception) else discovery[3]

    if not s2_scenes:
        raise ValueError("No Sentinel-2 scene available for core HyperFusion branch.")

    s2_scene = min(
        s2_scenes,
        key=lambda item: item.cloud_cover if item.cloud_cover is not None else 101.0,
    )
    spectral = await analyze_sentinel2_serverless(s2_scene, bbox)

    evidence: dict[str, np.ndarray] = {"spectral": spectral["score"]}
    branches: dict[str, Any] = {"spectral": spectral["summary"]}
    provenance: dict[str, Any] = {"spectral": spectral["provenance"]}
    branch_heatmaps: dict[str, Any] = {"spectral": spectral["heatmap"]}
    limitations: dict[str, str] = {}

    # Use the same 192x192 grid for all optional branches so fusion remains pixel-aligned.
    # SAR/DEM/Thermal helper defaults are adjusted by upsampling requests below when needed.
    if s1_scenes:
        try:
            sar = await analyze_sar_serverless(s1_scenes, bbox)
            if sar["score"].shape != spectral["score"].shape:
                limitations["sar"] = (
                    f"SAR shape {sar['score'].shape} differs from spectral "
                    f"{spectral['score'].shape}; branch retained for reporting but excluded from fusion."
                )
            else:
                evidence["sar"] = sar["score"]
            branches["sar"] = sar["summary"]
            provenance["sar"] = sar["provenance"]
            branch_heatmaps["sar"] = sar["heatmap"]
        except Exception as exc:
            limitations["sar"] = f"{type(exc).__name__}: {exc}"

    if dem_scenes:
        try:
            terrain = await analyze_dem_serverless(dem_scenes, bbox)
            if terrain["score"].shape != spectral["score"].shape:
                limitations["terrain"] = (
                    f"DEM shape {terrain['score'].shape} differs from spectral "
                    f"{spectral['score'].shape}; branch retained for reporting but excluded from fusion."
                )
            else:
                evidence["terrain"] = terrain["score"]
            branches["terrain"] = terrain["summary"]
            provenance["terrain"] = terrain["provenance"]
            branch_heatmaps["terrain"] = terrain["heatmap"]
        except Exception as exc:
            limitations["terrain"] = f"{type(exc).__name__}: {exc}"

    if thermal_scenes:
        try:
            thermal = await analyze_thermal_serverless(thermal_scenes, bbox)
            if thermal["score"].shape != spectral["score"].shape:
                limitations["thermal"] = (
                    f"Thermal shape {thermal['score'].shape} differs from spectral "
                    f"{spectral['score'].shape}; branch retained for reporting but excluded from fusion."
                )
            else:
                evidence["thermal"] = thermal["score"]
            branches["thermal"] = thermal["summary"]
            provenance["thermal"] = thermal["provenance"]
            branch_heatmaps["thermal"] = thermal["heatmap"]
        except Exception as exc:
            limitations["thermal"] = f"{type(exc).__name__}: {exc}"

    if end.year >= 2023:
        try:
            from app.pipeline.olmoearth_embeddings import analyze_olmoearth_embeddings

            olmo_year = min(2025, end.year)
            olmo = await analyze_olmoearth_embeddings(
                bbox,
                year=olmo_year,
                dims=64,
                target_shape=spectral["score"].shape,
            )
            if olmo["score"].shape != spectral["score"].shape:
                limitations["olmoearth"] = (
                    f"OlmoEarth shape {olmo['score'].shape} differs from spectral "
                    f"{spectral['score'].shape}; excluded from fusion."
                )
            else:
                evidence[f"olmoearth_{olmo_year}"] = olmo["score"]
            branches[f"olmoearth_{olmo_year}"] = olmo["summary"]
            provenance[f"olmoearth_{olmo_year}"] = olmo["provenance"]
            branch_heatmaps[f"olmoearth_{olmo_year}"] = olmo["heatmap"]
        except Exception as exc:
            limitations["olmoearth"] = f"{type(exc).__name__}: {exc}"
    else:
        limitations["olmoearth"] = (
            "Global OlmoEarth v1.3 embeddings are only guaranteed for 2023-2025."
        )

    fusion = fuse_serverless_layers(evidence, bbox, profile=fusion_profile)
    return {
        "real_data": True,
        "branch_heatmaps": branch_heatmaps if include_branch_heatmaps else None,
        "engine": "horus-hyperfusion-serverless-v1",
        "branches_used": list(evidence),
        "branches": branches,
        "provenance": provenance,
        "fusion": {
            "summary": fusion["summary"],
            "heatmap": fusion["heatmap"],
            "provenance": fusion["provenance"],
        },
        "discovery": {
            "sentinel2_count": len(s2_scenes),
            "sentinel1_count": len(s1_scenes),
            "dem_count": len(dem_scenes),
            "landsat_thermal_count": len(thermal_scenes),
            "enmap_count": 0,
            "emit_count": 0,
            "olmoearth_year": min(2025, end.year) if end.year >= 2023 else None,
            "olmoearth_source": "Source Cooperative / Ai2",
        },
        "limitations": limitations,
        "discovery_errors": discovery_errors,
        "scientific_status": (
            "Real-data HyperFusion using Planetary Computer EO layers plus optional "
            "OlmoEarth v1.3 global embeddings. Scores are evidence ranks, not calibrated "
            "archaeological probabilities."
        ),
    }
