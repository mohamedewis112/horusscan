from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from rasterio.enums import Resampling
from sklearn.ensemble import IsolationForest

from app.pipeline.raster_baseline import _asset_href, _heatmap_geojson, _read_bbox


@dataclass(frozen=True)
class SensorResult:
    sensor: str
    summary: dict[str, Any]
    heatmap: dict[str, Any]
    score: np.ndarray
    provenance: dict[str, Any]


def _scale_anomaly(raw: np.ndarray, valid: np.ndarray) -> np.ndarray:
    output = np.full(valid.shape, np.nan, dtype=np.float32)
    values = raw[valid]
    if values.size < 256:
        raise ValueError("Insufficient valid pixels for anomaly analysis.")
    low, high = np.quantile(values, [0.01, 0.99])
    output[valid] = np.clip((values - low) / max(high - low, 1e-6), 0.0, 1.0)
    return output


def _isolation_score(features: list[np.ndarray]) -> np.ndarray:
    stack = np.stack(features, axis=-1).astype(np.float32)
    valid = np.all(np.isfinite(stack), axis=-1)
    samples = stack[valid]
    if samples.shape[0] < 256:
        raise ValueError("Insufficient valid pixels for anomaly analysis.")
    model = IsolationForest(
        n_estimators=240,
        contamination="auto",
        random_state=42,
        n_jobs=-1,
    )
    model.fit(samples)
    raw_values = -model.score_samples(samples)
    raw = np.full(valid.shape, np.nan, dtype=np.float32)
    raw[valid] = raw_values.astype(np.float32)
    return _scale_anomaly(raw, valid)


def _summary(score: np.ndarray, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    valid = score[np.isfinite(score)]
    base: dict[str, Any] = {
        "status": "model_inference" if valid.size else "insufficient_evidence",
        "valid_pixels": int(valid.size),
        "score_type": "unsupervised_anomaly_rank",
        "not_probability": True,
    }
    if valid.size:
        base.update(
            p50=float(np.quantile(valid, 0.50)),
            p90=float(np.quantile(valid, 0.90)),
            p99=float(np.quantile(valid, 0.99)),
        )
    if extra:
        base.update(extra)
    return base


def analyze_sar_stack(
    scenes: list[Any],
    bbox: tuple[float, float, float, float],
) -> SensorResult:
    vv_stack: list[np.ndarray] = []
    vh_stack: list[np.ndarray] = []
    used: list[dict[str, Any]] = []

    for scene in scenes:
        try:
            vv = _read_bbox(_asset_href(scene.assets, ("vv", "VV")), bbox)
            vh = _read_bbox(_asset_href(scene.assets, ("vh", "VH")), bbox)
        except (KeyError, ValueError, OSError):
            continue
        if np.isfinite(vv).sum() < 256 or np.isfinite(vh).sum() < 256:
            continue
        vv_stack.append(vv)
        vh_stack.append(vh)
        used.append(
            {
                "provider": scene.provider,
                "collection": scene.collection,
                "item_id": scene.item_id,
                "datetime": scene.datetime,
            }
        )

    if not vv_stack:
        raise ValueError("No usable Sentinel-1 RTC VV/VH observations were available.")

    vv_cube = np.stack(vv_stack)
    vh_cube = np.stack(vh_stack)

    vv_med = np.nanmedian(vv_cube, axis=0)
    vh_med = np.nanmedian(vh_cube, axis=0)
    eps = np.float32(1e-6)
    log_vv = 10.0 * np.log10(np.maximum(vv_med, eps))
    log_vh = 10.0 * np.log10(np.maximum(vh_med, eps))
    ratio = log_vv - log_vh
    temporal_var = np.nanvar(log_vv, axis=0) if log_vv.ndim == 3 else np.nanvar(
        10.0 * np.log10(np.maximum(vv_cube, eps)), axis=0
    )
    grad_y, grad_x = np.gradient(log_vv)
    texture = np.hypot(grad_x, grad_y)

    score = _isolation_score([log_vv, log_vh, ratio, temporal_var, texture])
    return SensorResult(
        sensor="sentinel-1-rtc",
        summary=_summary(
            score,
            {
                "observations_used": len(used),
                "temporal_stack": len(used) > 1,
            },
        ),
        heatmap=_heatmap_geojson(score, bbox),
        score=score,
        provenance={"scenes": used, "features": ["VV", "VH", "VV-VH dB", "temporal_variance", "gradient_texture"]},
    )


def analyze_terrain(
    scenes: list[Any],
    bbox: tuple[float, float, float, float],
) -> SensorResult:
    tiles: list[np.ndarray] = []
    used: list[dict[str, Any]] = []

    for scene in scenes:
        href = None
        for key in ("data", "elevation", "dem"):
            asset = scene.assets.get(key)
            if isinstance(asset, dict) and asset.get("href"):
                href = str(asset["href"])
                break
        if href is None:
            for asset in scene.assets.values():
                if isinstance(asset, dict) and asset.get("href"):
                    href = str(asset["href"])
                    break
        if href is None:
            continue
        try:
            tile = _read_bbox(href, bbox, resampling=Resampling.bilinear)
        except (ValueError, OSError):
            continue
        if np.isfinite(tile).sum() < 256:
            continue
        tiles.append(tile)
        used.append(
            {
                "provider": scene.provider,
                "collection": scene.collection,
                "item_id": scene.item_id,
            }
        )

    if not tiles:
        raise ValueError("No usable Copernicus DEM tiles were available.")

    elevation = np.nanmedian(np.stack(tiles), axis=0)
    gy, gx = np.gradient(elevation)
    slope_proxy = np.hypot(gx, gy)
    gyy, _ = np.gradient(gy)
    _, gxx = np.gradient(gx)
    curvature = gxx + gyy
    roughness = np.hypot(*np.gradient(slope_proxy))

    score = _isolation_score([elevation, slope_proxy, curvature, roughness])
    return SensorResult(
        sensor="copernicus-dem-glo-30",
        summary=_summary(score, {"tiles_used": len(used)}),
        heatmap=_heatmap_geojson(score, bbox),
        score=score,
        provenance={
            "scenes": used,
            "features": ["elevation", "slope_proxy", "curvature", "terrain_roughness"],
            "note": "Terrain derivatives are relative analytical features, not survey-grade elevation products.",
        },
    )


def fuse_evidence_layers(
    layers: dict[str, np.ndarray],
    bbox: tuple[float, float, float, float],
    reliability: dict[str, float] | None = None,
) -> SensorResult:
    if not layers:
        raise ValueError("At least one evidence layer is required for fusion.")

    shapes = {arr.shape for arr in layers.values()}
    if len(shapes) != 1:
        raise ValueError(f"Evidence layers must share one raster shape, got: {sorted(shapes)}")

    names = list(layers)
    reliability = reliability or {}
    cube = np.stack([layers[name] for name in names], axis=0).astype(np.float32)
    valid = np.isfinite(cube)

    weights = np.asarray(
        [max(float(reliability.get(name, 1.0)), 1e-3) for name in names],
        dtype=np.float32,
    )[:, None, None]
    effective_weights = weights * valid
    weight_sum = np.sum(effective_weights, axis=0)

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

    high_votes = np.sum((cube >= 0.75) * valid, axis=0)
    valid_count = np.sum(valid, axis=0)
    agreement = np.divide(
        high_votes,
        valid_count,
        out=np.zeros_like(geometric, dtype=np.float32),
        where=valid_count > 0,
    )

    coverage = np.divide(
        valid_count,
        len(names),
        out=np.zeros_like(geometric, dtype=np.float32),
        where=len(names) > 0,
    )

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

    fused_valid = fused[np.isfinite(fused)]
    strong_fraction = (
        float(np.mean(agreement[np.isfinite(fused)] >= 0.66))
        if fused_valid.size
        else 0.0
    )
    if strong_fraction >= 0.25:
        evidence_agreement = "strong"
    elif strong_fraction >= 0.08:
        evidence_agreement = "moderate"
    else:
        evidence_agreement = "conflicting"

    return SensorResult(
        sensor="horus-hyperfusion-v0",
        summary=_summary(
            fused,
            {
                "sensors": names,
                "evidence_agreement": evidence_agreement,
                "agreement_fraction": strong_fraction,
                "mean_sensor_coverage": float(np.nanmean(coverage)),
                "fusion_method": "reliability_weighted_geometric_evidence+agreement+coverage_gating",
                "calibrated_probability": False,
            },
        ),
        heatmap=_heatmap_geojson(fused, bbox),
        score=fused,
        provenance={
            "inputs": names,
            "reliability_weights": {
                name: float(reliability.get(name, 1.0)) for name in names
            },
            "note": (
                "HyperFusion v0 is deterministic evidence fusion. "
                "It is not a calibrated archaeological probability."
            ),
        },
    )


def fuse_sensor_scores(
    spectral: np.ndarray,
    sar: np.ndarray | None,
    terrain: np.ndarray | None,
    bbox: tuple[float, float, float, float],
) -> SensorResult:
    layers = {"spectral": spectral}
    if sar is not None:
        layers["sar"] = sar
    if terrain is not None:
        layers["terrain"] = terrain
    return fuse_evidence_layers(layers, bbox)
