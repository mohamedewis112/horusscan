from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.warp import transform_bounds
from rasterio.windows import from_bounds

from app.pipeline.sentinel2 import SpectralFeatures, compute_spectral_features, spectral_anomaly_score


@dataclass(frozen=True)
class BaselineResult:
    scene_id: str
    cloud_cover: float | None
    summary: dict[str, Any]
    heatmap: dict[str, Any]
    score: np.ndarray


def _asset_href(assets: dict[str, Any], names: tuple[str, ...]) -> str:
    for name in names:
        asset = assets.get(name)
        if isinstance(asset, dict) and asset.get("href"):
            return str(asset["href"])
    raise KeyError(f"Missing required Sentinel-2 asset. Tried: {', '.join(names)}")


def _read_bbox(
    href: str,
    bbox_wgs84: tuple[float, float, float, float],
    shape: tuple[int, int] = (256, 256),
    resampling: Resampling = Resampling.bilinear,
) -> np.ndarray:
    with rasterio.Env(GDAL_HTTP_MULTIRANGE="YES", GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR"):
        with rasterio.open(href) as src:
            bounds = transform_bounds("EPSG:4326", src.crs, *bbox_wgs84, densify_pts=21)
            window = from_bounds(*bounds, transform=src.transform)
            data = src.read(
                1,
                window=window,
                out_shape=shape,
                resampling=resampling,
                masked=True,
            )
    return data.filled(np.nan).astype(np.float32)


def _heatmap_geojson(score: np.ndarray, bbox: tuple[float, float, float, float]) -> dict[str, Any]:
    min_lon, min_lat, max_lon, max_lat = bbox
    height, width = score.shape
    stride = max(1, min(height, width) // 32)
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


def analyze_scene(
    scene_id: str,
    cloud_cover: float | None,
    assets: dict[str, Any],
    bbox: tuple[float, float, float, float],
) -> BaselineResult:
    red = _read_bbox(_asset_href(assets, ("red", "B04")), bbox)
    nir = _read_bbox(_asset_href(assets, ("nir", "nir08", "B08")), bbox)
    swir1 = _read_bbox(_asset_href(assets, ("swir16", "swir1", "B11")), bbox)
    blue = _read_bbox(_asset_href(assets, ("blue", "B02")), bbox)

    features: SpectralFeatures = compute_spectral_features(red, nir, swir1, blue)
    valid_mask = np.isfinite(red) & np.isfinite(nir) & np.isfinite(swir1) & np.isfinite(blue)

    scl_href = None
    try:
        scl_href = _asset_href(assets, ("scl", "SCL"))
    except KeyError:
        pass

    if scl_href:
        scl = _read_bbox(scl_href, bbox, resampling=Resampling.nearest)
        invalid_scl = np.isin(scl.astype(np.int16), [0, 1, 3, 8, 9, 10, 11])
        valid_mask &= ~invalid_scl

    score = spectral_anomaly_score(features, valid_mask)
    valid = score[np.isfinite(score)]
    summary = {
        "status": "model_inference" if valid.size else "insufficient_evidence",
        "valid_pixels": int(valid.size),
        "score_type": "unsupervised_anomaly_rank",
        "not_probability": True,
    }
    if valid.size:
        summary.update(
            {
                "p50": float(np.quantile(valid, 0.50)),
                "p90": float(np.quantile(valid, 0.90)),
                "p99": float(np.quantile(valid, 0.99)),
            }
        )

    return BaselineResult(
        scene_id=scene_id,
        cloud_cover=cloud_cover,
        summary=summary,
        heatmap=_heatmap_geojson(score, bbox),
        score=score,
    )
