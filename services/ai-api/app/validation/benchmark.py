from __future__ import annotations

from dataclasses import dataclass
from math import cos, pi
from typing import Any

import numpy as np
from sklearn.metrics import average_precision_score, precision_recall_curve, roc_auc_score


@dataclass(frozen=True)
class ValidationCase:
    name: str
    case_type: str
    lat: float
    lon: float
    radius_m: int
    site_id: str | None = None


@dataclass(frozen=True)
class CaseEvaluation:
    rediscovery_score: float
    center_block_percentile: float
    concentration_ratio: float
    detected_high_priority: bool


def bbox_from_center(lat: float, lon: float, radius_m: int, margin: float = 2.2) -> tuple[float, float, float, float]:
    expanded = radius_m * margin
    lat_delta = expanded / 111_320.0
    lon_scale = max(cos(lat * pi / 180.0), 0.2)
    lon_delta = expanded / (111_320.0 * lon_scale)
    return lon - lon_delta, lat - lat_delta, lon + lon_delta, lat + lat_delta


def _approx_distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    mean_lat = (lat1 + lat2) / 2.0
    dy = (lat2 - lat1) * 111_320.0
    dx = (lon2 - lon1) * 111_320.0 * max(cos(mean_lat * pi / 180.0), 0.2)
    return float(np.hypot(dx, dy))


def deterministic_control(
    lat: float,
    lon: float,
    radius_m: int,
    seed_index: int,
    forbidden_centers: list[tuple[float, float, int]] | None = None,
) -> ValidationCase:
    # Same broad landscape context but spatially separated from all public known-site centers.
    # Controls remain UNLABELED: they are never asserted to be archaeology-free.
    forbidden_centers = forbidden_centers or []
    lon_scale = 111_320.0 * max(cos(lat * pi / 180.0), 0.2)

    for attempt in range(16):
        angle = ((seed_index * 137.507764) + attempt * 47.0) * pi / 180.0
        distance_m = radius_m * (6.0 + (attempt % 5))
        north_m = np.sin(angle) * distance_m
        east_m = np.cos(angle) * distance_m
        lat2 = lat + north_m / 111_320.0
        lon2 = lon + east_m / lon_scale

        safe = True
        for forbidden_lat, forbidden_lon, forbidden_radius in forbidden_centers:
            minimum = max(5_000.0, 3.0 * max(radius_m, forbidden_radius))
            if _approx_distance_m(lat2, lon2, forbidden_lat, forbidden_lon) < minimum:
                safe = False
                break

        if safe:
            return ValidationCase(
                name=f"Unlabeled control near {lat:.3f},{lon:.3f}",
                case_type="control_unlabeled",
                lat=lat2,
                lon=lon2,
                radius_m=radius_m,
                site_id=None,
            )

    raise ValueError("Could not generate a control sufficiently separated from known public sites.")


def evaluate_heatmap(
    heatmap: dict[str, Any],
    center_lat: float,
    center_lon: float,
    radius_m: int,
) -> CaseEvaluation:
    features = heatmap.get("features", [])
    if not features:
        raise ValueError("Heatmap contains no evaluable points.")

    distances: list[float] = []
    scores: list[float] = []

    lat_scale = 111_320.0
    lon_scale = 111_320.0 * max(cos(center_lat * pi / 180.0), 0.2)

    for feature in features:
        coords = feature.get("geometry", {}).get("coordinates")
        value = feature.get("properties", {}).get("anomaly_score")
        if not coords or value is None:
            continue
        lon, lat = float(coords[0]), float(coords[1])
        dx = (lon - center_lon) * lon_scale
        dy = (lat - center_lat) * lat_scale
        distances.append(float(np.hypot(dx, dy)))
        scores.append(float(value))

    if not scores:
        raise ValueError("Heatmap contains no numeric anomaly scores.")

    d = np.asarray(distances, dtype=np.float32)
    s = np.asarray(scores, dtype=np.float32)

    inner = s[d <= radius_m]
    context = s[(d > radius_m) & (d <= radius_m * 2.2)]
    if inner.size == 0:
        nearest_idx = np.argsort(d)[: max(1, min(4, len(d)))]
        inner = s[nearest_idx]
    if context.size < 4:
        context = s

    center_score = float(np.nanmean(inner))
    context_mean = float(np.nanmean(context))
    concentration_ratio = center_score / max(context_mean, 1e-6)

    percentile = float(np.mean(s <= center_score))
    rediscovery_score = float(
        np.clip(
            0.65 * percentile + 0.35 * np.clip((concentration_ratio - 0.8) / 0.8, 0.0, 1.0),
            0.0,
            1.0,
        )
    )

    detected = bool(rediscovery_score >= 0.75 and percentile >= 0.80)

    return CaseEvaluation(
        rediscovery_score=rediscovery_score,
        center_block_percentile=percentile,
        concentration_ratio=concentration_ratio,
        detected_high_priority=detected,
    )


def compute_benchmark_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    usable = [
        row for row in rows
        if row.get("rediscovery_score") is not None
        and row.get("case_type") in {"positive", "control_unlabeled"}
    ]
    positives = [row for row in usable if row["case_type"] == "positive"]
    controls = [row for row in usable if row["case_type"] == "control_unlabeled"]

    if not positives:
        return {"status": "insufficient_positive_cases"}

    positive_hits = sum(bool(row.get("detected_high_priority")) for row in positives)
    control_flags = sum(bool(row.get("detected_high_priority")) for row in controls)

    labels = np.array([1 if row["case_type"] == "positive" else 0 for row in usable], dtype=int)
    scores = np.array([float(row["rediscovery_score"]) for row in usable], dtype=float)

    high_priority = [row for row in usable if row.get("detected_high_priority")]
    hp_true = sum(row["case_type"] == "positive" for row in high_priority)

    metrics: dict[str, Any] = {
        "positive_cases": len(positives),
        "control_unlabeled_cases": len(controls),
        "known_site_rediscovery_rate": positive_hits / len(positives),
        "control_high_priority_flag_rate_proxy": (
            control_flags / len(controls) if controls else None
        ),
        "precision_at_high_priority_proxy": (
            hp_true / len(high_priority) if high_priority else None
        ),
        "high_priority_count": len(high_priority),
        "interpretation": {
            "controls": "Unlabeled controls are not confirmed negatives.",
            "probability": "Rediscovery scores are ranking metrics, not archaeological probabilities.",
        },
    }

    if len(np.unique(labels)) == 2:
        metrics["pr_auc_proxy"] = float(average_precision_score(labels, scores))
        metrics["roc_auc_proxy"] = float(roc_auc_score(labels, scores))
        precision, recall, thresholds = precision_recall_curve(labels, scores)
        metrics["pr_curve"] = {
            "precision": [float(x) for x in precision],
            "recall": [float(x) for x in recall],
            "thresholds": [float(x) for x in thresholds],
        }

    return metrics
