from __future__ import annotations

import asyncio
from datetime import datetime

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.pipeline.raster_baseline import analyze_scene
from app.providers.stac import search_open_cog_sentinel2, search_sentinel2


app = FastAPI(
    title="HorusScan AI API",
    version="0.1.0",
    description="Remote-sensing analysis service. No fake scores or excavation claims.",
)


class SceneSearchRequest(BaseModel):
    bbox: tuple[float, float, float, float]
    start: datetime
    end: datetime
    max_cloud: float = Field(default=20.0, ge=0.0, le=100.0)
    limit: int = Field(default=10, ge=1, le=100)


class BaselineAnalysisRequest(BaseModel):
    bbox: tuple[float, float, float, float]
    start: datetime
    end: datetime
    max_cloud: float = Field(default=20.0, ge=0.0, le=100.0)


def validate_bbox(bbox: tuple[float, float, float, float]) -> None:
    min_lon, min_lat, max_lon, max_lat = bbox
    if min_lon >= max_lon or min_lat >= max_lat:
        raise HTTPException(status_code=422, detail="Invalid bounding box.")
    if not (-180 <= min_lon <= 180 and -180 <= max_lon <= 180):
        raise HTTPException(status_code=422, detail="Longitude outside valid range.")
    if not (-90 <= min_lat <= 90 and -90 <= max_lat <= 90):
        raise HTTPException(status_code=422, detail="Latitude outside valid range.")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "horusscan-ai-api"}


@app.post("/v1/scenes/sentinel-2")
async def discover_sentinel2(request: SceneSearchRequest) -> dict:
    validate_bbox(request.bbox)
    scenes = await search_sentinel2(
        request.bbox,
        request.start,
        request.end,
        max_cloud=request.max_cloud,
        limit=request.limit,
    )
    return {
        "source": "Copernicus Data Space STAC",
        "real_data": True,
        "count": len(scenes),
        "scenes": [scene.__dict__ for scene in scenes],
    }


@app.post("/v1/analysis/sentinel-2-baseline")
async def run_sentinel2_baseline(request: BaselineAnalysisRequest) -> dict:
    validate_bbox(request.bbox)

    scenes = await search_open_cog_sentinel2(
        request.bbox,
        request.start,
        request.end,
        max_cloud=request.max_cloud,
        limit=5,
    )
    if not scenes:
        raise HTTPException(
            status_code=404,
            detail="No suitable Sentinel-2 L2A observations found for the requested AOI/date range.",
        )

    scene = min(
        scenes,
        key=lambda item: item.cloud_cover if item.cloud_cover is not None else 101.0,
    )

    try:
        result = await asyncio.to_thread(
            analyze_scene,
            scene.item_id,
            scene.cloud_cover,
            scene.assets,
            request.bbox,
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Real raster processing failed: {type(exc).__name__}: {exc}",
        ) from exc

    return {
        "real_data": True,
        "source": scene.provider,
        "scene": {
            "id": result.scene_id,
            "datetime": scene.datetime,
            "cloud_cover": result.cloud_cover,
        },
        "summary": result.summary,
        "heatmap": result.heatmap,
        "scientific_status": (
            "Unsupervised spectral anomaly baseline. "
            "It is not an archaeological probability or excavation confirmation."
        ),
    }
