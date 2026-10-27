from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Annotated

import httpx\nfrom fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.persistence import PersistenceUnavailable, SupabasePersistence
from app.pipeline.multisensor import analyze_sar_stack, analyze_terrain, fuse_sensor_scores
from app.pipeline.raster_baseline import analyze_scene
from app.providers.stac import (
    search_copernicus_dem,
    search_open_cog_sentinel2,
    search_sentinel1_rtc,
    search_sentinel2,
)


app = FastAPI(
    title="HorusScan AI API",
    version="0.2.0",
    description="Remote-sensing analysis service. No fake scores or excavation claims.",
)

allowed_origins = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["authorization", "content-type"],
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


class MultisensorAnalysisRequest(BaselineAnalysisRequest):
    project_id: str | None = None
    aoi_id: str | None = None
    analysis_run_id: str | None = None


def validate_bbox(bbox: tuple[float, float, float, float]) -> None:
    min_lon, min_lat, max_lon, max_lat = bbox
    if min_lon >= max_lon or min_lat >= max_lat:
        raise HTTPException(status_code=422, detail="Invalid bounding box.")
    if not (-180 <= min_lon <= 180 and -180 <= max_lon <= 180):
        raise HTTPException(status_code=422, detail="Longitude outside valid range.")
    if not (-90 <= min_lat <= 90 and -90 <= max_lat <= 90):
        raise HTTPException(status_code=422, detail="Latitude outside valid range.")


def bearer_from_header(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Invalid Authorization header.")
    return token


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "horusscan-ai-api", "version": "0.2.0"}


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


@app.post("/v1/analysis/multisensor-baseline")
async def run_multisensor_baseline(
    request: MultisensorAnalysisRequest,
    authorization: Annotated[str | None, Header()] = None,
) -> dict:
    validate_bbox(request.bbox)

    persistence: SupabasePersistence | None = None
    user_id: str | None = None
    persist_requested = all(
        [request.project_id, request.aoi_id, request.analysis_run_id, authorization]
    )

    if persist_requested:
        try:
            persistence = SupabasePersistence()
            token = bearer_from_header(authorization)
            assert token is not None
            user_id = await persistence.authenticate(token)
            await persistence.assert_run_ownership(
                user_id,
                request.project_id or "",
                request.aoi_id or "",
                request.analysis_run_id or "",
            )
            await persistence.update_run(
                request.analysis_run_id or "",
                {
                    "status": "discovering",
                    "progress_stage": "multisensor_data_discovery",
                    "started_at": datetime.utcnow().isoformat() + "Z",\n                    "git_commit": os.getenv("GIT_COMMIT_SHA"),
                },
            )
        except (PersistenceUnavailable, httpx.HTTPError, PermissionError) as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    try:
        s2_task = search_open_cog_sentinel2(
            request.bbox,
            request.start,
            request.end,
            max_cloud=request.max_cloud,
            limit=5,
        )
        s1_task = asyncio.to_thread(
            search_sentinel1_rtc,
            request.bbox,
            request.start,
            request.end,
            6,
        )
        dem_task = asyncio.to_thread(search_copernicus_dem, request.bbox, 8)
        s2_scenes, s1_scenes, dem_scenes = await asyncio.gather(s2_task, s1_task, dem_task)

        if not s2_scenes:
            raise HTTPException(
                status_code=404,
                detail="No suitable Sentinel-2 observations found for the requested AOI/date range.",
            )

        s2_scene = min(
            s2_scenes,
            key=lambda item: item.cloud_cover if item.cloud_cover is not None else 101.0,
        )

        if persistence and request.analysis_run_id:
            await persistence.update_run(
                request.analysis_run_id,
                {"status": "preprocessing", "progress_stage": "sensor_preprocessing"},
            )

        spectral = await asyncio.to_thread(
            analyze_scene,
            s2_scene.item_id,
            s2_scene.cloud_cover,
            s2_scene.assets,
            request.bbox,
        )

        sar = None
        sar_error = None
        if s1_scenes:
            try:
                sar = await asyncio.to_thread(analyze_sar_stack, s1_scenes, request.bbox)
            except (ValueError, KeyError, OSError) as exc:
                sar_error = str(exc)

        terrain = None
        terrain_error = None
        if dem_scenes:
            try:
                terrain = await asyncio.to_thread(analyze_terrain, dem_scenes, request.bbox)
            except (ValueError, KeyError, OSError) as exc:
                terrain_error = str(exc)

        fusion = await asyncio.to_thread(
            fuse_sensor_scores,
            spectral.score,
            sar.score if sar else None,
            terrain.score if terrain else None,
            request.bbox,
        )

        if persistence and request.analysis_run_id:
            await persistence.update_run(
                request.analysis_run_id,
                {"status": "fusing", "progress_stage": "evidence_fusion"},
            )

            scene_records = [(s2_scene, "spectral", 0)]
            scene_records.extend((scene, "sar", idx) for idx, scene in enumerate(s1_scenes))
            scene_records.extend((scene, "terrain", idx) for idx, scene in enumerate(dem_scenes))

            for scene, role, ordinal in scene_records:
                scene_id = await persistence.store_scene(scene, role)
                await persistence.link_scene(
                    request.analysis_run_id,
                    scene_id,
                    role,
                    ordinal,
                )

            await persistence.store_layer(
                request.analysis_run_id,
                "spectral_anomaly",
                spectral.summary,
                {
                    "provider": s2_scene.provider,
                    "collection": s2_scene.collection,
                    "scene_id": s2_scene.item_id,
                    "datetime": s2_scene.datetime,
                    "bands": ["B02", "B04", "B08", "B11"],
                },
                spectral.heatmap,
            )
            if sar:
                await persistence.store_layer(
                    request.analysis_run_id,
                    "sar_anomaly",
                    sar.summary,
                    sar.provenance,
                    sar.heatmap,
                )
            if terrain:
                await persistence.store_layer(
                    request.analysis_run_id,
                    "terrain_anomaly",
                    terrain.summary,
                    terrain.provenance,
                    terrain.heatmap,
                )
            await persistence.store_layer(
                request.analysis_run_id,
                "multisensor_fusion",
                fusion.summary,
                fusion.provenance,
                fusion.heatmap,
            )
            await persistence.update_run(
                request.analysis_run_id,
                {
                    "status": "completed",
                    "progress_stage": "complete",
                    "data_quality_score": float(
                        min(
                            1.0,
                            (
                                0.5
                                + (0.25 if sar is not None else 0.0)
                                + (0.25 if terrain is not None else 0.0)
                            ),
                        )
                    ),
                    "finished_at": datetime.utcnow().isoformat() + "Z",
                },
            )

        return {
            "real_data": True,
            "persisted": bool(persistence and request.analysis_run_id),
            "sources": {
                "spectral": s2_scene.provider,
                "sar": s1_scenes[0].provider if s1_scenes else None,
                "terrain": dem_scenes[0].provider if dem_scenes else None,
            },
            "spectral": {
                "scene": {
                    "id": s2_scene.item_id,
                    "datetime": s2_scene.datetime,
                    "cloud_cover": s2_scene.cloud_cover,
                },
                "summary": spectral.summary,
            },
            "sar": {
                "summary": sar.summary if sar else None,
                "error": sar_error,
            },
            "terrain": {
                "summary": terrain.summary if terrain else None,
                "error": terrain_error,
            },
            "fusion": {
                "summary": fusion.summary,
                "heatmap": fusion.heatmap,
            },
            "scientific_status": (
                "Multisensor anomaly-ranking baseline using real optical, SAR and terrain evidence. "
                "Scores are not calibrated archaeological probabilities and do not confirm subsurface structures."
            ),
        }
    except HTTPException:
        raise
    except Exception as exc:
        if persistence and request.analysis_run_id:
            try:
                await persistence.update_run(
                    request.analysis_run_id,
                    {
                        "status": "failed",
                        "progress_stage": "failed",
                        "error_message": f"{type(exc).__name__}: {exc}",
                        "finished_at": datetime.utcnow().isoformat() + "Z",
                    },
                )
            except Exception:
                pass
        raise HTTPException(
            status_code=502,
            detail=f"Multisensor processing failed: {type(exc).__name__}: {exc}",
        ) from exc
