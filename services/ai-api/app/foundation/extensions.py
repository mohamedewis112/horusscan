from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import numpy as np

from app.foundation.client import infer_remote
from app.foundation.registry import get_model_registry


def heatmap_to_grid(
    heatmap: dict[str, Any],
    bbox: tuple[float, float, float, float],
    shape: tuple[int, int],
) -> np.ndarray:
    min_lon, min_lat, max_lon, max_lat = bbox
    height, width = shape
    grid = np.full(shape, np.nan, dtype=np.float32)
    counts = np.zeros(shape, dtype=np.int32)

    for feature in heatmap.get("features", []):
        coords = feature.get("geometry", {}).get("coordinates")
        value = feature.get("properties", {}).get("anomaly_score")
        if not isinstance(coords, list) or len(coords) < 2:
            continue
        if not isinstance(value, (int, float)):
            continue
        lon, lat = float(coords[0]), float(coords[1])
        if not (min_lon <= lon <= max_lon and min_lat <= lat <= max_lat):
            continue
        col = min(width - 1, max(0, int((lon - min_lon) / max(max_lon - min_lon, 1e-9) * width)))
        row = min(height - 1, max(0, int((max_lat - lat) / max(max_lat - min_lat, 1e-9) * height)))
        if np.isfinite(grid[row, col]):
            grid[row, col] += float(value)
        else:
            grid[row, col] = float(value)
        counts[row, col] += 1

    filled = np.isfinite(grid)
    grid[filled] = grid[filled] / np.maximum(counts[filled], 1)

    for _ in range(3):
        missing = ~np.isfinite(grid)
        if not missing.any():
            break
        padded = np.pad(grid, 1, mode="constant", constant_values=np.nan)
        neighbors = []
        for dy in range(3):
            for dx in range(3):
                if dx == 1 and dy == 1:
                    continue
                neighbors.append(padded[dy:dy + height, dx:dx + width])
        stack = np.stack(neighbors)
        with np.errstate(invalid="ignore"):
            estimate = np.nanmean(stack, axis=0)
        fill = missing & np.isfinite(estimate)
        grid[fill] = estimate[fill]

    return grid


async def load_available_external_models(
    bbox: tuple[float, float, float, float],
    start: datetime,
    end: datetime,
    target_shape: tuple[int, int],
) -> tuple[dict[str, np.ndarray], dict[str, Any], dict[str, Any], dict[str, str]]:
    excluded = {"olmoearth", "sam3_1"}
    ready = [
        row for row in get_model_registry()
        if row["key"] not in excluded
        and row.get("available")
        and row.get("endpoint_configured")
    ]
    if not ready:
        return {}, {}, {}, {}

    async def run_one(row: dict[str, Any]):
        key = str(row["key"])
        try:
            result = await infer_remote(
                key,
                {
                    "task": "spatial_anomaly_heatmap",
                    "bbox": bbox,
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "output_contract": {
                        "real_inference": True,
                        "heatmap": "GeoJSON point features with anomaly_score",
                    },
                },
                timeout_seconds=180.0,
            )
            heatmap = result.get("heatmap")
            if not isinstance(heatmap, dict):
                raise RuntimeError("Endpoint returned no heatmap.")
            grid = heatmap_to_grid(heatmap, bbox, target_shape)
            values = grid[np.isfinite(grid)]
            if values.size < 64:
                raise RuntimeError("External model heatmap was too sparse after alignment.")
            summary = {
                "status": "model_inference",
                "valid_pixels": int(values.size),
                "score_type": f"{key}_external_rank",
                "not_probability": True,
                "p50": float(np.quantile(values, 0.50)),
                "p90": float(np.quantile(values, 0.90)),
                "p99": float(np.quantile(values, 0.99)),
                "model_metadata": result.get("model_metadata", {}),
            }
            return key, grid, summary, result.get("provenance", {}), None
        except Exception as exc:
            return key, None, None, None, f"{type(exc).__name__}: {exc}"

    results = await asyncio.gather(*(run_one(row) for row in ready))
    layers: dict[str, np.ndarray] = {}
    summaries: dict[str, Any] = {}
    provenance: dict[str, Any] = {}
    limitations: dict[str, str] = {}
    for key, grid, summary, prov, error in results:
        if error:
            limitations[key] = error
            continue
        if grid is None or summary is None:
            limitations[key] = "Model returned no usable output."
            continue
        layers[key] = grid
        summaries[key] = summary
        provenance[key] = prov or {}
    return layers, summaries, provenance, limitations
