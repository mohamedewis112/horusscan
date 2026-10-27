from __future__ import annotations

import asyncio
from typing import Any

import numpy as np

from app.pipeline.serverless_raster import (
    ServerlessScene,
    read_pc_item_assets_npy,
)


def _robust_z(values: np.ndarray, valid: np.ndarray) -> np.ndarray:
    samples = values[valid & np.isfinite(values)]
    if samples.size < 64:
        return np.full(values.shape, np.nan, dtype=np.float32)
    median = float(np.median(samples))
    mad = float(np.median(np.abs(samples - median)))
    scale = max(1.4826 * mad, 1e-6)
    result = np.full(values.shape, np.nan, dtype=np.float32)
    good = valid & np.isfinite(values)
    result[good] = np.abs((values[good] - median) / scale).astype(np.float32)
    return result


def _rank_score(features: list[np.ndarray], valid: np.ndarray) -> np.ndarray:
    z = [_robust_z(feature, valid) for feature in features]
    stack = np.stack(z)
    raw = np.sqrt(np.nanmean(np.square(stack), axis=0))
    good = valid & np.isfinite(raw)
    samples = raw[good]
    if samples.size < 64:
        raise ValueError("Insufficient valid pixels for physics-SAR corroboration.")
    low, high = np.quantile(samples, [0.02, 0.98])
    score = np.full(raw.shape, np.nan, dtype=np.float32)
    score[good] = np.clip(
        (raw[good] - low) / max(float(high - low), 1e-6),
        0.0,
        1.0,
    ).astype(np.float32)
    return score


def _heatmap(
    score: np.ndarray,
    bbox: tuple[float, float, float, float],
) -> dict[str, Any]:
    min_lon, min_lat, max_lon, max_lat = bbox
    height, width = score.shape
    stride = max(1, min(height, width) // 32)
    features: list[dict[str, Any]] = []
    for row in range(0, height, stride):
        for col in range(0, width, stride):
            block = score[row:row + stride, col:col + stride]
            values = block[np.isfinite(block)]
            if not values.size:
                continue
            lon = min_lon + ((col + stride / 2) / width) * (max_lon - min_lon)
            lat = max_lat - ((row + stride / 2) / height) * (max_lat - min_lat)
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {"anomaly_score": float(np.mean(values))},
            })
    return {"type": "FeatureCollection", "features": features}


async def analyze_physics_sar(
    scenes: list[ServerlessScene],
    bbox: tuple[float, float, float, float],
) -> dict[str, Any]:
    usable = [scene for scene in scenes if {"vv", "vh"}.issubset(scene.assets)][:4]
    if not usable:
        raise ValueError("No Sentinel-1 RTC VV/VH scenes for physics-SAR corroboration.")

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

    vv_rows: list[np.ndarray] = []
    vh_rows: list[np.ndarray] = []
    used: list[ServerlessScene] = []
    for scene, result in zip(usable, reads):
        if isinstance(result, Exception):
            continue
        cube, mask = result
        vv, vh = cube
        valid = mask & np.isfinite(vv) & np.isfinite(vh) & (vv > 0) & (vh > 0)
        if int(valid.sum()) < 256:
            continue
        vv_rows.append(np.where(valid, vv, np.nan))
        vh_rows.append(np.where(valid, vh, np.nan))
        used.append(scene)

    if not vv_rows:
        raise ValueError("Sentinel-1 reads contained no usable physics-SAR pixels.")

    vv_cube = np.stack(vv_rows)
    vh_cube = np.stack(vh_rows)
    eps = np.float32(1e-8)

    vv = np.nanmedian(vv_cube, axis=0)
    vh = np.nanmedian(vh_cube, axis=0)
    log_vv = 10.0 * np.log10(np.maximum(vv, eps))
    log_vh = 10.0 * np.log10(np.maximum(vh, eps))

    polarization_ratio_db = log_vv - log_vh
    cross_pol_fraction = vh / np.maximum(vv + vh, eps)
    temporal_cv = np.nanstd(vv_cube, axis=0) / np.maximum(np.nanmean(vv_cube, axis=0), eps)
    gy, gx = np.gradient(log_vv)
    roughness_proxy = np.hypot(gx, gy)

    valid = (
        np.isfinite(log_vv)
        & np.isfinite(log_vh)
        & np.isfinite(polarization_ratio_db)
        & np.isfinite(cross_pol_fraction)
        & np.isfinite(temporal_cv)
        & np.isfinite(roughness_proxy)
    )

    score = _rank_score(
        [
            polarization_ratio_db,
            cross_pol_fraction,
            temporal_cv,
            roughness_proxy,
        ],
        valid,
    )

    values = score[np.isfinite(score)]
    return {
        "score": score,
        "heatmap": _heatmap(score, bbox),
        "summary": {
            "status": "model_inference",
            "valid_pixels": int(values.size),
            "score_type": "sentinel1_physics_informed_corroboration_rank",
            "not_probability": True,
            "p50": float(np.quantile(values, 0.50)),
            "p90": float(np.quantile(values, 0.90)),
            "p99": float(np.quantile(values, 0.99)),
            "observations_used": len(used),
            "adjudication_only": True,
            "claim": "physics-informed radar corroboration, not calibrated soil-moisture inversion",
        },
        "provenance": {
            "provider": "Microsoft Planetary Computer",
            "collection": "sentinel-1-rtc",
            "scenes": [scene.item_id for scene in used],
            "assets": ["vv", "vh"],
            "features": [
                "VV/VH dB polarization ratio",
                "cross-pol fraction",
                "VV temporal coefficient of variation",
                "VV spatial gradient roughness proxy",
            ],
            "adjudication_only": True,
        },
    }
