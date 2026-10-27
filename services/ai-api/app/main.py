from __future__ import annotations

from datetime import datetime

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.providers.stac import search_sentinel2


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


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "horusscan-ai-api"}


@app.post("/v1/scenes/sentinel-2")
async def discover_sentinel2(request: SceneSearchRequest) -> dict:
    min_lon, min_lat, max_lon, max_lat = request.bbox
    if min_lon >= max_lon or min_lat >= max_lat:
        raise HTTPException(status_code=422, detail="Invalid bounding box.")
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
