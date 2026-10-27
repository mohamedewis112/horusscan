from __future__ import annotations

import asyncio
from datetime import datetime
from io import BytesIO
from typing import Any

import httpx
import numpy as np
import torch
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from terratorch.registry import BACKBONE_REGISTRY


PC_STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
PC_TILER = "https://planetarycomputer.microsoft.com/api/data/v1"

MODEL_KEY = "prithvi_eo_2"
MODEL_NAME = "prithvi_eo_v2_tiny_tl"
BANDS = ("B02", "B03", "B04", "B8A", "B11", "B12")
MEAN = np.asarray([1087.0, 1342.0, 1433.0, 2734.0, 1958.0, 1363.0], dtype=np.float32)
STD = np.asarray([2248.0, 2179.0, 2178.0, 1850.0, 1242.0, 1049.0], dtype=np.float32)

app = FastAPI(title="HorusScan Prithvi Worker", version="0.1.0")
_model = None


class Request(BaseModel):
    task: str
    bbox: tuple[float, float, float, float]
    start: str
    end: str
    max_cloud: float = 20.0
    output_contract: dict[str, Any] | None = None


async def _search(bbox, start, end, max_cloud):
    payload = {
        "collections": ["sentinel-2-l2a"],
        "bbox": list(bbox),
        "datetime": f"{start}/{end}",
        "limit": 8,
        "query": {"eo:cloud_cover": {"lte": max_cloud}},
        "sortby": [{"field": "properties.datetime", "direction": "asc"}],
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(f"{PC_STAC}/search", json=payload)
        response.raise_for_status()
        items = response.json().get("features", [])
    if len(items) < 1:
        raise RuntimeError("No Sentinel-2 scenes found.")
    if len(items) >= 4:
        indices = np.linspace(0, len(items)-1, 4).round().astype(int)
        return [items[int(i)] for i in indices]
    while len(items) < 4:
        items.append(items[-1])
    return items[:4]


async def _read_scene(item, bbox):
    min_lon, min_lat, max_lon, max_lat = bbox
    url = f"{PC_TILER}/item/bbox/{min_lon},{min_lat},{max_lon},{max_lat}/224x224.npy"
    params = [
        ("collection", "sentinel-2-l2a"),
        ("item", item["id"]),
        ("asset_as_band", "true"),
        ("return_mask", "true"),
        ("resampling", "bilinear"),
        ("unscale", "false"),
    ]
    params.extend(("assets", band) for band in BANDS)
    async with httpx.AsyncClient(timeout=90.0) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
    arr = np.load(BytesIO(response.content), allow_pickle=False)
    data = arr[:6].astype(np.float32)
    mask = arr[-1] > 0 if arr.shape[0] > 6 else np.all(np.isfinite(data), axis=0)
    data[:, ~mask] = np.nan
    return data


def _load_model():
    global _model
    if _model is None:
        _model = BACKBONE_REGISTRY.build(
            MODEL_NAME,
            pretrained=True,
            num_frames=4,
            coords_encoding=["time", "location"],
        )
        _model = _model.to("cpu").eval()
    return _model


def _infer_sync(cubes, datetimes, bbox):
    model = _load_model()
    stack = np.stack(cubes, axis=1)
    for channel in range(6):
        stack[channel] = (stack[channel] - MEAN[channel]) / STD[channel]
    stack = np.nan_to_num(stack, nan=0.0001).astype(np.float32)
    x = torch.from_numpy(stack).unsqueeze(0)

    times = []
    for value in datetimes:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        times.append([float(dt.year), float(dt.timetuple().tm_yday - 1)])
    temporal = torch.tensor([times], dtype=torch.float32)

    min_lon, min_lat, max_lon, max_lat = bbox
    location = torch.tensor(
        [[(min_lon + max_lon) / 2.0, (min_lat + max_lat) / 2.0]],
        dtype=torch.float32,
    )

    with torch.inference_mode():
        outputs = model(x, temporal_coords=temporal, location_coords=location)

    tokens = outputs[-1] if isinstance(outputs, (list, tuple)) else outputs
    if isinstance(tokens, (list, tuple)):
        tokens = tokens[-1]
    if tokens.ndim != 3:
        raise RuntimeError(f"Unexpected Prithvi token shape: {tuple(tokens.shape)}")

    tokens = tokens[0]
    if tokens.shape[0] == 785:
        tokens = tokens[1:]
    if tokens.shape[0] != 784:
        raise RuntimeError(f"Expected 784 patch tokens, got {tokens.shape[0]}.")

    grid = tokens.reshape(4, 14, 14, -1).mean(dim=0)
    flat = torch.nn.functional.normalize(grid.reshape(-1, grid.shape[-1]).float(), dim=-1)
    center = torch.nn.functional.normalize(flat.mean(dim=0, keepdim=True), dim=-1)
    distance = (1.0 - (flat @ center.T).squeeze(-1)).cpu().numpy()
    lo, hi = np.quantile(distance, [0.01, 0.99])
    score = np.clip((distance - lo) / max(float(hi-lo), 1e-6), 0.0, 1.0).reshape(14,14)

    features = []
    for row in range(14):
        for col in range(14):
            lon = min_lon + ((col + 0.5) / 14) * (max_lon-min_lon)
            lat = max_lat - ((row + 0.5) / 14) * (max_lat-min_lat)
            features.append({
                "type":"Feature",
                "geometry":{"type":"Point","coordinates":[lon,lat]},
                "properties":{"anomaly_score":float(score[row,col])},
            })
    return {
        "type":"FeatureCollection",
        "features":features,
    }, int(tokens.shape[-1])


@app.get("/health")
async def health():
    return {
        "status":"ok",
        "model_key":MODEL_KEY,
        "model_name":MODEL_NAME,
        "device":"cpu",
        "model_loaded":_model is not None,
    }


@app.post("/infer")
async def infer(request: Request):
    try:
        items = await _search(
            request.bbox,
            request.start,
            request.end,
            request.max_cloud,
        )
        cubes = await asyncio.gather(*[_read_scene(item, request.bbox) for item in items])
        dates = [
            item.get("properties", {}).get("datetime")
            or item.get("properties", {}).get("start_datetime")
            for item in items
        ]
        heatmap, dim = await asyncio.to_thread(
            _infer_sync,
            cubes,
            dates,
            request.bbox,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Prithvi real inference failed: {type(exc).__name__}: {exc}",
        ) from exc

    return {
        "real_inference":True,
        "heatmap":heatmap,
        "model_metadata":{
            "model_key":MODEL_KEY,
            "model_name":MODEL_NAME,
            "parameters":"5M",
            "embedding_dim":dim,
            "device":"cpu",
        },
        "provenance":{
            "provider":"Microsoft Planetary Computer + IBM/NASA Prithvi-EO-2.0",
            "sentinel2_items":[item["id"] for item in items],
            "method":"Prithvi temporal patch-embedding distance-to-local-context",
            "not_probability":True,
        },
    }


@app.get("/smoke")
async def smoke():
    request = Request(
        task="spatial_anomaly_heatmap",
        bbox=(31.20, 29.92, 31.22, 29.94),
        start="2026-04-01T00:00:00Z",
        end="2026-10-26T23:59:59Z",
        max_cloud=25.0,
    )
    result = await infer(request)
    features = result.get("heatmap", {}).get("features", [])
    scores = [
        float(item.get("properties", {}).get("anomaly_score"))
        for item in features
        if isinstance(item.get("properties", {}).get("anomaly_score"), (int, float))
    ]
    return {
        "status":"completed",
        "real_inference":result.get("real_inference") is True,
        "model_metadata":result.get("model_metadata", {}),
        "feature_count":len(features),
        "score_min":min(scores) if scores else None,
        "score_max":max(scores) if scores else None,
        "provenance":result.get("provenance", {}),
    }
