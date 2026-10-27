import numpy as np

from app.pipeline.sentinel2 import compute_spectral_features, spectral_anomaly_score


def test_spectral_features_are_finite() -> None:
    red = np.full((20, 20), 0.2, dtype=np.float32)
    nir = np.full((20, 20), 0.6, dtype=np.float32)
    swir = np.full((20, 20), 0.3, dtype=np.float32)
    blue = np.full((20, 20), 0.1, dtype=np.float32)

    features = compute_spectral_features(red, nir, swir, blue)

    assert np.all(np.isfinite(features.ndvi))
    assert np.allclose(features.ndvi, 0.5)


def test_anomaly_baseline_returns_rank_map() -> None:
    rng = np.random.default_rng(42)
    base = rng.normal(0.2, 0.02, size=(40, 40)).astype(np.float32)
    red = base
    nir = base + 0.25
    swir = base + 0.08
    blue = base - 0.05
    features = compute_spectral_features(red, nir, swir, blue)
    score = spectral_anomaly_score(features, np.ones((40, 40), dtype=bool))

    assert score.shape == (40, 40)
    assert float(np.nanmin(score)) >= 0.0
    assert float(np.nanmax(score)) <= 1.0
