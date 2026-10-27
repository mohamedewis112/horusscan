from __future__ import annotations

import asyncio
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import onnxruntime as ort
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

PC_STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
PC_TILER = "https://planetarycomputer.microsoft.com/api/data/v1"
MODEL_URL = "https://huggingface.co/nthh/prithvi-eo-tiny-onnx/resolve/main/encoder.onnx"
MODEL_PATH = Path("/tmp/prithvi-eo-2-tiny.onnx")

MEAN = np.asarray([1087, 1342, 1433, 2734, 1958, 1363], dtype=np.float32)[:, None, None]
STD = np.asarray([2248, 2179, 2178, 1850, 1242, 1049], dtype=np.float32)[:, None, None]

app = FastAPI(title="HorusScan Prithvi Worker", version="0.3.0")
_session: ort.InferenceSession | None = None
_lock = asyncio.Lock()


class InferenceRequest(BaseModel):
    task: str = "spatial_anomaly_heatmap"
    bbox: tuple[float, float, float, float]
    start: datetime
    end: datetime
    grid_size: int = Field(default=3, ge=2, le=4)
    output_contract: dict[str, Any] | None = None


def validate_bbox(bbox: tuple[float, float, float, float]) -> None:
    min_lon, min_lat, max_lon, max_lat = bbox
    if min_lon >= max_lon or min_lat >= max_lat:
        raise ValueError("Invalid bbox.")
    if max_lon - min_lon > 0.35 or max_lat - min_lat > 0.35:
        raise ValueError("AOI is too large for the lightweight Prithvi worker.")


async def get_session() -> ort.InferenceSession:
    global _session
    if _session is not None:
        return _session
    async with _lock:
        if _session is None:
            if not MODEL_PATH.exists() or MODEL_PATH.stat().st_size < 20_000_000:
                async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
                    response = await client.get(MODEL_URL)
                    response.raise_for_status()
                    MODEL_PATH.write_bytes(response.content)
            options = ort.SessionOptions()
            options.intra_op_num_threads = 2
            options.inter_op_num_threads = 1
            _session = await asyncio.to_thread(
                ort.InferenceSession,
                str(MODEL_PATH),
                sess_options=options,
                providers=["CPUExecutionProvider"],
            )
    return _session


async def search_hls(
    bbox: tuple[float, float, float, float],
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    payload = {
        "collections": ["hls2-s30", "hls2-l30"],
        "bbox": list(bbox),
        "datetime": f"{start.isoformat()}/{end.isoformat()}",
        "limit": 20,
        "sortby": [{"field": "properties.datetime", "direction": "desc"}],
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(f"{PC_STAC}/search", json=payload)
        response.raise_for_status()
        features = response.json().get("features", [])
    if not features:
        raise ValueError("No HLS v2 scene found.")
    return min(
        features,
        key=lambda item: (
            float(item.get("properties", {}).get("eo:cloud_cover") or 101.0),
            str(item.get("id", "")),
        ),
    )


def asset_order(collection: str) -> tuple[str, ...]:
    if collection == "hls2-s30":
        return ("B02", "B03", "B04", "B8A", "B11", "B12")
    if collection == "hls2-l30":
        return ("B02", "B03", "B04", "B05", "B06", "B07")
    raise ValueError(f"Unsupported HLS collection: {collection}")


async def read_patch(
    scene: dict[str, Any],
    bbox: tuple[float, float, float, float],
) -> np.ndarray:
    collection = str(scene["collection"])
    assets = asset_order(collection)
    min_lon, min_lat, max_lon, max_lat = bbox
    url = f"{PC_TILER}/item/bbox/{min_lon},{min_lat},{max_lon},{max_lat}/224x224.npy"
    params: list[tuple[str, str]] = [
        ("collection", collection),
        ("item", str(scene["id"])),
        ("asset_as_band", "true"),
        ("return_mask", "true"),
        ("resampling", "bilinear"),
        ("unscale", "false"),
    ]
    params.extend(("assets", asset) for asset in assets)
    async with httpx.AsyncClient(timeout=90.0) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
    array = np.load(BytesIO(response.content), allow_pickle=False)
    if array.ndim != 3 or array.shape[0] < 6:
        raise RuntimeError(f"Unexpected HLS tile shape: {array.shape}")
    cube = array[:6].astype(np.float32)
    mask = array[-1] > 0 if array.shape[0] > 6 else np.all(np.isfinite(cube), axis=0)
    cube = np.where(np.isfinite(cube), cube, MEAN)
    if float(np.nanmedian(np.abs(cube))) < 20.0:
        cube = cube * 10_000.0
    cube = np.where(mask[None, :, :], cube, MEAN)
    normalized = (cube - MEAN) / STD
    return normalized[None, :, None, :, :].astype(np.float32)


def split_bbox(
    bbox: tuple[float, float, float, float],
    grid_size: int,
) -> list[tuple[float, float, float, float]]:
    min_lon, min_lat, max_lon, max_lat = bbox
    cells = []
    for row in range(grid_size):
        lat_hi = max_lat - (max_lat - min_lat) * row / grid_size
        lat_lo = max_lat - (max_lat - min_lat) * (row + 1) / grid_size
        for col in range(grid_size):
            lon_lo = min_lon + (max_lon - min_lon) * col / grid_size
            lon_hi = min_lon + (max_lon - min_lon) * (col + 1) / grid_size
            cells.append((lon_lo, lat_lo, lon_hi, lat_hi))
    return cells


async def embed(session: ort.InferenceSession, patch: np.ndarray) -> np.ndarray:
    input_name = session.get_inputs()[0].name
    output_name = session.get_outputs()[0].name
    output = await asyncio.to_thread(session.run, [output_name], {input_name: patch})
    embedding = np.asarray(output[0], dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(embedding))
    return embedding / norm if norm > 1e-6 else embedding


def anomaly_scores(embeddings: np.ndarray) -> np.ndarray:
    center = np.mean(embeddings, axis=0)
    center /= max(float(np.linalg.norm(center)), 1e-6)
    distance = 1.0 - np.clip(embeddings @ center, -1.0, 1.0)
    low, high = np.quantile(distance, [0.05, 0.95])
    return np.clip((distance - low) / max(float(high - low), 1e-6), 0.0, 1.0)


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "model": "Prithvi-EO-2.0-tiny-TL",
        "runtime": "onnxruntime-cpu",
        "real_inference": True,
    }


@app.post("/infer")
async def infer(request: InferenceRequest) -> dict[str, Any]:
    try:
        validate_bbox(request.bbox)
        scene = await search_hls(request.bbox, request.start, request.end)
        session = await get_session()
        cells = split_bbox(request.bbox, request.grid_size)
        patches = await asyncio.gather(*[read_patch(scene, cell) for cell in cells])
        embeddings = np.stack(
            await asyncio.gather(*[embed(session, patch) for patch in patches])
        )
        scores = anomaly_scores(embeddings)

        features = []
        for cell, score in zip(cells, scores):
            min_lon, min_lat, max_lon, max_lat = cell
            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [(min_lon + max_lon) / 2.0, (min_lat + max_lat) / 2.0],
                },
                "properties": {"anomaly_score": float(score)},
            })

        return {
            "real_inference": True,
            "status": "completed",
            "heatmap": {"type": "FeatureCollection", "features": features},
            "model_metadata": {
                "model_key": "prithvi_eo_2",
                "model_name": "Prithvi-EO-2.0-tiny-TL",
                "parameters": "5M",
                "runtime": "onnxruntime-cpu",
                "embedding_dimensions": int(embeddings.shape[1]),
                "grid_size": request.grid_size,
                "score_type": "embedding_cosine_anomaly_rank",
                "not_probability": True,
            },
            "provenance": {
                "checkpoint": MODEL_URL,
                "base_model": "ibm-nasa-geospatial/Prithvi-EO-2.0-tiny-TL",
                "hls_collection": scene["collection"],
                "hls_item_id": scene["id"],
                "hls_datetime": scene.get("properties", {}).get("datetime"),
                "hls_assets": list(asset_order(str(scene["collection"]))),
                "provider": "Microsoft Planetary Computer + public ONNX export",
            },
        }
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Prithvi inference failed: {type(exc).__name__}: {exc}",
        ) from exc


@app.get("/smoke")
async def smoke() -> dict[str, Any]:
    result = await infer(
        InferenceRequest(
            bbox=(31.20, 29.92, 31.22, 29.94),
            start=datetime.fromisoformat("2026-04-01T00:00:00+00:00"),
            end=datetime.fromisoformat("2026-10-26T23:59:59+00:00"),
            grid_size=3,
        )
    )
    values = [
        float(feature["properties"]["anomaly_score"])
        for feature in result["heatmap"]["features"]
    ]
    return {
        "status": "completed",
        "real_inference": result["real_inference"],
        "model_metadata": result["model_metadata"],
        "feature_count": len(values),
        "score_min": min(values),
        "score_max": max(values),
        "provenance": result["provenance"],
    }
