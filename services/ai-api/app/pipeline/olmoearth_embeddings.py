from __future__ import annotations

import asyncio
from functools import lru_cache
from typing import Any

import numpy as np
import zarr
from obstore.store import S3Store
from pyproj import Transformer
from sklearn.decomposition import PCA
from sklearn.ensemble import IsolationForest


STORE_URL = "s3://olmoearth/olmoearth-v1-3-embeddings/embeddings.zarr"
SOURCE_ENDPOINT = "https://data.source.coop"


def _dequantize(q: np.ndarray) -> np.ndarray:
    q = np.asarray(q)
    values = np.where(q == -128, np.nan, q).astype(np.float32)
    return (np.abs(values) / 127.5) ** 2.0 * np.sign(values)


@lru_cache(maxsize=1)
def _root():
    zarr.config.set({"array.sharding_coalesce_max_gap_bytes": 0})
    s3 = S3Store.from_url(
        STORE_URL,
        endpoint=SOURCE_ENDPOINT,
        skip_signature=True,
    )
    store = zarr.storage.ObjectStore(s3, read_only=True)
    return zarr.open_group(store, mode="r")


def _zone_number(lon: float) -> int:
    return max(1, min(60, int((lon + 180.0) // 6.0) + 1))


def _nearest_resample(score: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    target_h, target_w = shape
    rows = np.linspace(0, score.shape[0] - 1, target_h).round().astype(int)
    cols = np.linspace(0, score.shape[1] - 1, target_w).round().astype(int)
    return score[np.ix_(rows, cols)].astype(np.float32)


def _embedding_anomaly(
    embedding: np.ndarray,
    valid: np.ndarray,
) -> np.ndarray:
    # embedding: bands x y x x
    pixels = np.moveaxis(embedding, 0, -1)
    samples = pixels[valid]
    if samples.shape[0] < 256:
        raise ValueError("Insufficient valid OlmoEarth embedding pixels.")

    n_components = min(12, samples.shape[1], max(2, samples.shape[0] - 1))
    pca = PCA(n_components=n_components, random_state=42)
    latent = pca.fit_transform(samples)

    model = IsolationForest(
        n_estimators=240,
        contamination="auto",
        random_state=42,
        n_jobs=-1,
    )
    model.fit(latent)
    raw = -model.score_samples(latent)
    low, high = np.quantile(raw, [0.01, 0.99])
    scaled = np.clip((raw - low) / max(float(high - low), 1e-6), 0.0, 1.0)

    output = np.full(valid.shape, np.nan, dtype=np.float32)
    output[valid] = scaled.astype(np.float32)
    return output


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
            block = score[row : row + stride, col : col + stride]
            values = block[np.isfinite(block)]
            if values.size == 0:
                continue
            lon = min_lon + ((col + stride / 2) / width) * (max_lon - min_lon)
            lat = max_lat - ((row + stride / 2) / height) * (max_lat - min_lat)
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [lon, lat]},
                    "properties": {"anomaly_score": float(np.mean(values))},
                }
            )
    return {"type": "FeatureCollection", "features": features}


def _read_olmoearth_sync(
    bbox: tuple[float, float, float, float],
    year: int,
    dims: int,
    target_shape: tuple[int, int],
) -> dict[str, Any]:
    if dims not in {64, 128}:
        raise ValueError("OlmoEarth embedding width must be 64 or 128.")

    min_lon, min_lat, max_lon, max_lat = bbox
    center_lon = (min_lon + max_lon) / 2.0
    center_lat = (min_lat + max_lat) / 2.0
    zone_num = _zone_number(center_lon)

    # Small AOIs must stay inside one UTM zone for a single read.
    if _zone_number(min_lon) != zone_num or _zone_number(max_lon) != zone_num:
        raise ValueError("AOI crosses a UTM-zone boundary; split AOI before OlmoEarth read.")

    root = _root()
    zone = root[f"utm{zone_num:02d}"]

    years = np.asarray(zone["time"][:]).astype(int)
    matches = np.where(years == year)[0]
    if matches.size == 0:
        raise ValueError(f"OlmoEarth year {year} is not present in this zone.")
    time_index = int(matches[0])

    crs = zone.attrs["proj:code"]
    transformer = Transformer.from_crs("EPSG:4326", crs, always_xy=True)

    corners = [
        transformer.transform(min_lon, min_lat),
        transformer.transform(min_lon, max_lat),
        transformer.transform(max_lon, min_lat),
        transformer.transform(max_lon, max_lat),
    ]
    xs = [point[0] for point in corners]
    ys = [point[1] for point in corners]

    a, _, c, _, e, f = zone.attrs["spatial:transform"]
    cols = [(x - c) / a for x in xs]
    rows = [(y - f) / e for y in ys]

    col0 = int(np.floor(min(cols)))
    col1 = int(np.ceil(max(cols)))
    row0 = int(np.floor(min(rows)))
    row1 = int(np.ceil(max(rows)))

    embeddings = zone["embeddings"]
    _, _, max_rows, max_cols = embeddings.shape
    col0 = max(0, min(max_cols - 1, col0))
    col1 = max(col0 + 1, min(max_cols, col1))
    row0 = max(0, min(max_rows - 1, row0))
    row1 = max(row0 + 1, min(max_rows, row1))

    q = np.asarray(
        embeddings[time_index, 0:dims, row0:row1, col0:col1]
    )
    if q.ndim != 3:
        raise RuntimeError(f"Unexpected OlmoEarth embedding shape: {q.shape}")

    valid = np.all(q != -128, axis=0)
    embedding = _dequantize(q)
    score_native = _embedding_anomaly(embedding, valid)
    score = _nearest_resample(score_native, target_shape)

    values = score[np.isfinite(score)]
    if values.size == 0:
        raise ValueError("OlmoEarth returned no valid embeddings for AOI.")

    return {
        "score": score,
        "heatmap": _heatmap(score, bbox),
        "summary": {
            "status": "model_inference",
            "valid_pixels": int(values.size),
            "score_type": "olmoearth_v1_3_embedding_anomaly_rank",
            "not_probability": True,
            "p50": float(np.quantile(values, 0.50)),
            "p90": float(np.quantile(values, 0.90)),
            "p99": float(np.quantile(values, 0.99)),
            "year": year,
            "embedding_dimensions": dims,
            "resolution_m": 10,
            "foundation_model": "OlmoEarth-v1.3-Base",
        },
        "provenance": {
            "provider": "Source Cooperative / Ai2",
            "store": STORE_URL,
            "model": "OlmoEarth-v1.3-Base",
            "year": year,
            "dimensions": dims,
            "utm_zone": zone_num,
            "crs": str(crs),
            "native_window": [row0, row1, col0, col1],
            "quantization": "signed_power_int8_dequantized_before_analysis",
        },
    }


async def analyze_olmoearth_embeddings(
    bbox: tuple[float, float, float, float],
    year: int = 2025,
    dims: int = 64,
    target_shape: tuple[int, int] = (192, 192),
) -> dict[str, Any]:
    return await asyncio.to_thread(
        _read_olmoearth_sync,
        bbox,
        year,
        dims,
        target_shape,
    )
