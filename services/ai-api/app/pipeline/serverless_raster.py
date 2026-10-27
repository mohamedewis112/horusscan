from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from typing import Any

import httpx
import numpy as np

from app.pipeline.sentinel2 import (
    compute_spectral_features,
    spectral_anomaly_score,
    summarize_score,
)
from app.pipeline.raster_baseline import _heatmap_geojson


PC_STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
PC_TILER = "https://planetarycomputer.microsoft.com/api/data/v1"


@dataclass(frozen=True)
class ServerlessScene:
    collection: str
    item_id: str
    datetime: str | None
    cloud_cover: float | None
    properties: dict[str, Any]


async def search_pc_sentinel2(
    bbox: tuple[float, float, float, float],
    start: datetime,
    end: datetime,
    max_cloud: float = 25.0,
    limit: int = 5,
) -> list[ServerlessScene]:
    payload = {
        "collections": ["sentinel-2-l2a"],
        "bbox": list(bbox),
        "datetime": f"{start.isoformat()}/{end.isoformat()}",
        "limit": limit,
        "query": {"eo:cloud_cover": {"lte": max_cloud}},
        "sortby": [{"field": "properties.datetime", "direction": "desc"}],
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(f"{PC_STAC}/search", json=payload)
        response.raise_for_status()
        features = response.json().get("features", [])

    return [
        ServerlessScene(
            collection=str(item.get("collection") or "sentinel-2-l2a"),
            item_id=str(item["id"]),
            datetime=item.get("properties", {}).get("datetime"),
            cloud_cover=item.get("properties", {}).get("eo:cloud_cover"),
            properties=item.get("properties", {}),
        )
        for item in features
    ]


async def read_pc_item_assets_npy(
    scene: ServerlessScene,
    bbox: tuple[float, float, float, float],
    assets: tuple[str, ...],
    width: int = 192,
    height: int = 192,
) -> tuple[np.ndarray, np.ndarray]:
    min_lon, min_lat, max_lon, max_lat = bbox
    url = (
        f"{PC_TILER}/item/bbox/"
        f"{min_lon},{min_lat},{max_lon},{max_lat}/{width}x{height}.npy"
    )

    params: list[tuple[str, str]] = [
        ("collection", scene.collection),
        ("item", scene.item_id),
        ("asset_as_band", "true"),
        ("return_mask", "true"),
        ("resampling", "bilinear"),
    ]
    params.extend(("assets", asset) for asset in assets)

    async with httpx.AsyncClient(timeout=90.0) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        if "application/x-binary" not in response.headers.get("content-type", ""):
            raise RuntimeError(
                "Planetary Computer tiler did not return NumpyTile binary: "
                f"{response.headers.get('content-type')}"
            )

    array = np.load(BytesIO(response.content), allow_pickle=False)
    if array.ndim != 3 or array.shape[0] < len(assets):
        raise RuntimeError(f"Unexpected NumpyTile shape: {array.shape}")

    data = array[: len(assets)].astype(np.float32)
    if array.shape[0] > len(assets):
        mask = array[-1] > 0
    else:
        mask = np.all(np.isfinite(data), axis=0)
    return data, mask


async def analyze_sentinel2_serverless(
    scene: ServerlessScene,
    bbox: tuple[float, float, float, float],
) -> dict[str, Any]:
    # Explicit order: blue, red, nir, swir1
    cube, mask = await read_pc_item_assets_npy(
        scene,
        bbox,
        ("B02", "B04", "B08", "B11"),
    )
    blue, red, nir, swir1 = cube

    valid = (
        mask
        & np.isfinite(blue)
        & np.isfinite(red)
        & np.isfinite(nir)
        & np.isfinite(swir1)
        & (blue > 0)
        & (red > 0)
        & (nir > 0)
        & (swir1 > 0)
    )

    features = compute_spectral_features(
        red=red,
        nir=nir,
        swir1=swir1,
        blue=blue,
    )
    score = spectral_anomaly_score(features, valid)
    summary = summarize_score(score)
    summary.update(
        {
            "provider": "microsoft-planetary-computer-titiler",
            "collection": scene.collection,
            "scene_id": scene.item_id,
            "serverless_raster_path": True,
        }
    )
    return {
        "summary": summary,
        "heatmap": _heatmap_geojson(score, bbox),
        "score": score,
    }
