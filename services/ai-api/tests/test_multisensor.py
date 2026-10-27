import numpy as np

from app.pipeline.multisensor import fuse_sensor_scores


def test_multisensor_fusion_is_bounded_and_reports_agreement() -> None:
    spectral = np.full((32, 32), 0.8, dtype=np.float32)
    sar = np.full((32, 32), 0.85, dtype=np.float32)
    terrain = np.full((32, 32), 0.78, dtype=np.float32)

    result = fuse_sensor_scores(
        spectral,
        sar,
        terrain,
        (30.0, 29.0, 30.1, 29.1),
    )

    valid = result.score[np.isfinite(result.score)]
    assert valid.size == 32 * 32
    assert float(valid.min()) >= 0.0
    assert float(valid.max()) <= 1.0
    assert result.summary["evidence_agreement"] == "strong"
    assert result.summary["not_probability"] is True


def test_fusion_survives_missing_optional_sensor() -> None:
    spectral = np.linspace(0.0, 1.0, 1024, dtype=np.float32).reshape(32, 32)

    result = fuse_sensor_scores(
        spectral,
        None,
        None,
        (30.0, 29.0, 30.1, 29.1),
    )

    assert result.summary["sensors"] == ["spectral"]
    assert result.score.shape == (32, 32)
