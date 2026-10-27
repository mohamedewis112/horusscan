from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from sklearn.ensemble import IsolationForest


@dataclass(frozen=True)
class SpectralFeatures:
    ndvi: np.ndarray
    ndmi: np.ndarray
    bsi: np.ndarray


def safe_ratio(numerator: np.ndarray, denominator: np.ndarray) -> np.ndarray:
    return np.divide(
        numerator,
        denominator,
        out=np.zeros_like(numerator, dtype=np.float32),
        where=np.abs(denominator) > 1e-6,
    )


def compute_spectral_features(
    red: np.ndarray,
    nir: np.ndarray,
    swir1: np.ndarray,
    blue: np.ndarray,
) -> SpectralFeatures:
    red = red.astype(np.float32)
    nir = nir.astype(np.float32)
    swir1 = swir1.astype(np.float32)
    blue = blue.astype(np.float32)

    ndvi = safe_ratio(nir - red, nir + red)
    ndmi = safe_ratio(nir - swir1, nir + swir1)
    bsi = safe_ratio((swir1 + red) - (nir + blue), (swir1 + red) + (nir + blue))
    return SpectralFeatures(ndvi=ndvi, ndmi=ndmi, bsi=bsi)


def spectral_anomaly_score(features: SpectralFeatures, valid_mask: np.ndarray) -> np.ndarray:
    """Unsupervised baseline. Scores are anomaly ranks, not archaeological probabilities."""
    stack = np.stack([features.ndvi, features.ndmi, features.bsi], axis=-1)
    valid = valid_mask & np.all(np.isfinite(stack), axis=-1)
    output = np.full(valid.shape, np.nan, dtype=np.float32)

    samples = stack[valid]
    if samples.shape[0] < 256:
        raise ValueError("Insufficient valid pixels for anomaly analysis.")

    model = IsolationForest(
        n_estimators=200,
        contamination="auto",
        random_state=42,
        n_jobs=-1,
    )
    model.fit(samples)
    raw = -model.score_samples(samples)
    low, high = np.quantile(raw, [0.01, 0.99])
    scaled = np.clip((raw - low) / max(high - low, 1e-6), 0.0, 1.0)
    output[valid] = scaled.astype(np.float32)
    return output


def summarize_score(score: np.ndarray) -> dict[str, Any]:
    valid = score[np.isfinite(score)]
    if valid.size == 0:
        return {"status": "insufficient_evidence", "valid_pixels": 0}
    return {
        "status": "model_inference",
        "valid_pixels": int(valid.size),
        "p50": float(np.quantile(valid, 0.50)),
        "p90": float(np.quantile(valid, 0.90)),
        "p99": float(np.quantile(valid, 0.99)),
        "note": "Anomaly rank only; not a probability of archaeological presence.",
    }
