import numpy as np

from app.pipeline.multisensor import fuse_evidence_layers


def test_hyperfusion_accepts_optional_advanced_branches() -> None:
    shape = (24, 24)
    layers = {
        "spectral": np.full(shape, 0.80, dtype=np.float32),
        "sar": np.full(shape, 0.76, dtype=np.float32),
        "terrain": np.full(shape, 0.55, dtype=np.float32),
        "hyperspectral": np.full(shape, 0.88, dtype=np.float32),
        "thermal": np.full(shape, 0.72, dtype=np.float32),
    }
    result = fuse_evidence_layers(layers, (30.0, 29.0, 30.1, 29.1))

    values = result.score[np.isfinite(result.score)]
    assert values.size == shape[0] * shape[1]
    assert float(values.min()) >= 0.0
    assert float(values.max()) <= 1.0
    assert set(result.summary["sensors"]) == set(layers)
    assert result.summary["calibrated_probability"] is False


def test_hyperfusion_handles_partial_missing_pixels() -> None:
    spectral = np.full((20, 20), 0.7, dtype=np.float32)
    sar = np.full((20, 20), 0.8, dtype=np.float32)
    sar[:5, :] = np.nan

    result = fuse_evidence_layers(
        {"spectral": spectral, "sar": sar},
        (30.0, 29.0, 30.1, 29.1),
    )

    assert np.isfinite(result.score).all()
    assert result.summary["mean_sensor_coverage"] < 1.0
