import numpy as np

from app.validation.benchmark import (
    ValidationCase,
    compute_benchmark_metrics,
    deterministic_control,
    evaluate_heatmap,
)


def _heatmap(center_lon: float, center_lat: float, peak: bool) -> dict:
    features = []
    for iy in range(-5, 6):
        for ix in range(-5, 6):
            lon = center_lon + ix * 0.001
            lat = center_lat + iy * 0.001
            distance = (ix * ix + iy * iy) ** 0.5
            score = 0.2 + (0.75 * np.exp(-distance / 1.4) if peak else 0.05 * np.sin(ix + iy))
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [lon, lat]},
                    "properties": {"anomaly_score": float(np.clip(score, 0.0, 1.0))},
                }
            )
    return {"type": "FeatureCollection", "features": features}


def test_known_site_peak_is_rediscovered() -> None:
    result = evaluate_heatmap(
        _heatmap(31.26, 29.85, peak=True),
        center_lat=29.85,
        center_lon=31.26,
        radius_m=250,
    )
    assert result.rediscovery_score >= 0.75
    assert result.detected_high_priority is True
    assert result.concentration_ratio > 1.0


def test_flat_control_is_not_high_priority() -> None:
    result = evaluate_heatmap(
        _heatmap(31.26, 29.85, peak=False),
        center_lat=29.85,
        center_lon=31.26,
        radius_m=250,
    )
    assert result.rediscovery_score < 0.75


def test_control_generation_is_deterministic_and_separated() -> None:
    first = deterministic_control(25.72, 32.65, 900, 2)
    second = deterministic_control(25.72, 32.65, 900, 2)
    assert first == second
    assert first.case_type == "control_unlabeled"
    assert abs(first.lat - 25.72) > 0.02
    assert abs(first.lon - 32.65) > 0.02


def test_metrics_separate_known_sites_and_unlabeled_controls() -> None:
    rows = [
        {"case_type": "positive", "rediscovery_score": 0.92, "detected_high_priority": True},
        {"case_type": "positive", "rediscovery_score": 0.83, "detected_high_priority": True},
        {"case_type": "positive", "rediscovery_score": 0.40, "detected_high_priority": False},
        {"case_type": "control_unlabeled", "rediscovery_score": 0.22, "detected_high_priority": False},
        {"case_type": "control_unlabeled", "rediscovery_score": 0.31, "detected_high_priority": False},
        {"case_type": "control_unlabeled", "rediscovery_score": 0.79, "detected_high_priority": True},
    ]
    metrics = compute_benchmark_metrics(rows)
    assert metrics["known_site_rediscovery_rate"] == 2 / 3
    assert metrics["precision_at_high_priority_proxy"] == 2 / 3
    assert 0.0 <= metrics["pr_auc_proxy"] <= 1.0
    assert metrics["interpretation"]["controls"].startswith("Unlabeled controls")
