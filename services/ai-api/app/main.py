from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Annotated

import httpx
import os
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.persistence import PersistenceUnavailable, SupabasePersistence
from app.pipeline.multisensor import analyze_sar_stack, analyze_terrain, fuse_sensor_scores
from app.pipeline.raster_baseline import analyze_scene
from app.validation.benchmark import ValidationCase, compute_benchmark_metrics, deterministic_control
from app.validation.runner import run_validation_case
from app.routes.advanced import router as advanced_router
from app.routes.tournament import router as tournament_router
from app.providers.stac import (
    search_copernicus_dem,
    search_open_cog_sentinel2,
    search_sentinel1_rtc,
    search_sentinel2,
)


app = FastAPI(
    title="HorusScan AI API",
    version="0.3.0",
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

app.include_router(advanced_router)
app.include_router(tournament_router)


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


class ValidationRunRequest(BaseModel):
    dataset_version: str = "unesco-egypt-public-v1-2026-10-27"
    split: str = "validation"
    start: datetime
    end: datetime
    max_cloud: float = Field(default=20.0, ge=0.0, le=100.0)
    include_unlabeled_controls: bool = True


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
    return {"status": "ok", "service": "horusscan-ai-api", "version": "0.3.0"}


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
                    "started_at": datetime.utcnow().isoformat() + "Z",
                    "git_commit": os.getenv("GIT_COMMIT_SHA"),
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


@app.post("/v1/validation/run")
async def run_validation_benchmark(
    request: ValidationRunRequest,
    authorization: Annotated[str | None, Header()] = None,
) -> dict:
    if request.split not in {"calibration", "validation", "blind_test"}:
        raise HTTPException(status_code=422, detail="Invalid validation split.")

    token = bearer_from_header(authorization)
    if token is None:
        raise HTTPException(status_code=401, detail="Authentication required.")

    try:
        persistence = SupabasePersistence()
        user_id = await persistence.authenticate(token)
        if request.split == "blind_test" and await persistence.has_locked_blind_run(
            user_id,
            request.dataset_version,
        ):
            raise HTTPException(
                status_code=409,
                detail="A blind test is already running or locked for this dataset. The canonical result cannot be rerun.",
            )
        sites = await persistence.list_validation_sites(
            request.dataset_version,
            request.split,
        )
        all_site_centers = await persistence.list_all_validation_centers(
            request.dataset_version,
        )
    except (PersistenceUnavailable, httpx.HTTPError, PermissionError) as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    if not sites:
        raise HTTPException(status_code=404, detail="No validation sites found for requested split.")

    total_cases = len(sites) * (2 if request.include_unlabeled_controls else 1)
    try:
        run_id = await persistence.create_validation_run(
            user_id=user_id,
            dataset_version=request.dataset_version,
            split=request.split,
            parameters={
                "start": request.start.isoformat(),
                "end": request.end.isoformat(),
                "max_cloud": request.max_cloud,
                "include_unlabeled_controls": request.include_unlabeled_controls,
                "method": "known_site_rediscovery_v1",
                "control_semantics": "unlabeled_not_confirmed_negative",
            },
            git_commit=os.getenv("GIT_COMMIT_SHA"),
        )
        await persistence.update_validation_run(run_id, {"total_cases": total_cases})
    except httpx.HTTPStatusError as exc:
        if request.split == "blind_test" and exc.response.status_code in {400, 409}:
            raise HTTPException(
                status_code=409,
                detail="Blind test is already running or locked for this dataset.",
            ) from exc
        raise HTTPException(
            status_code=502,
            detail=f"Could not create validation run: {exc.response.status_code}",
        ) from exc

    rows: list[dict] = []
    completed = 0
    forbidden_centers = [
        (
            float(site["latitude"]),
            float(site["longitude"]),
            int(site["evaluation_radius_m"]),
        )
        for site in all_site_centers
    ]

    try:
        for index, site in enumerate(sites):
            positive = ValidationCase(
                name=str(site["name"]),
                case_type="positive",
                lat=float(site["latitude"]),
                lon=float(site["longitude"]),
                radius_m=int(site["evaluation_radius_m"]),
                site_id=str(site["id"]),
            )

            cases = [positive]
            if request.include_unlabeled_controls:
                cases.append(
                    deterministic_control(
                        positive.lat,
                        positive.lon,
                        positive.radius_m,
                        index,
                        forbidden_centers=forbidden_centers,
                    )
                )

            for case in cases:
                try:
                    case_result = await run_validation_case(
                        case,
                        request.start,
                        request.end,
                        request.max_cloud,
                    )
                    evaluation = case_result["evaluation"]
                    row = {
                        "case_type": case.case_type,
                        "case_name": case.name,
                        "rediscovery_score": evaluation.rediscovery_score,
                        "center_block_percentile": evaluation.center_block_percentile,
                        "concentration_ratio": evaluation.concentration_ratio,
                        "detected_high_priority": evaluation.detected_high_priority,
                    }
                    rows.append(row)
                    await persistence.store_validation_result(
                        validation_run_id=run_id,
                        validation_site_id=case.site_id,
                        case_type=case.case_type,
                        case_name=case.name,
                        lon=case.lon,
                        lat=case.lat,
                        radius_m=case.radius_m,
                        rediscovery_score=evaluation.rediscovery_score,
                        center_block_percentile=evaluation.center_block_percentile,
                        concentration_ratio=evaluation.concentration_ratio,
                        detected_high_priority=evaluation.detected_high_priority,
                        fusion_summary=case_result["fusion_summary"],
                        sensor_summary=case_result["sensor_summary"],
                    )
                except Exception as case_exc:
                    rows.append(
                        {
                            "case_type": case.case_type,
                            "case_name": case.name,
                            "rediscovery_score": None,
                            "detected_high_priority": False,
                            "error": f"{type(case_exc).__name__}: {case_exc}",
                        }
                    )
                    await persistence.store_validation_result(
                        validation_run_id=run_id,
                        validation_site_id=case.site_id,
                        case_type=case.case_type,
                        case_name=case.name,
                        lon=case.lon,
                        lat=case.lat,
                        radius_m=case.radius_m,
                        rediscovery_score=None,
                        center_block_percentile=None,
                        concentration_ratio=None,
                        detected_high_priority=None,
                        fusion_summary={},
                        sensor_summary={},
                        error_message=f"{type(case_exc).__name__}: {case_exc}",
                    )

                completed += 1
                await persistence.update_validation_run(
                    run_id,
                    {"completed_cases": completed},
                )

        metrics = compute_benchmark_metrics(rows)
        successful_positive_cases = sum(
            row.get("case_type") == "positive" and row.get("rediscovery_score") is not None
            for row in rows
        )
        run_status = "completed" if successful_positive_cases == len(sites) else "inconclusive"
        await persistence.update_validation_run(
            run_id,
            {
                "status": run_status,
                "completed_cases": completed,
                "metrics": metrics,
                "finished_at": datetime.utcnow().isoformat() + "Z",
            },
        )
        response_payload = {
            "validation_run_id": run_id,
            "status": run_status,
            "dataset_version": request.dataset_version,
            "split": request.split,
            "completed_cases": completed,
            "total_cases": total_cases,
            "metrics": metrics,
            "scientific_note": (
                "Positive cases are public known archaeological sites. "
                "Controls are unlabeled comparison areas, not confirmed archaeological negatives. "
                "Proxy PR/ROC metrics must be interpreted accordingly."
            ),
        }
        if request.split != "blind_test":
            response_payload["cases"] = rows
        else:
            response_payload["blind_test"] = {
                "locked_after_run": True,
                "case_details_withheld": True,
                "message": "Blind-test case names and coordinates are withheld from the API response.",
            }
        return response_payload
    except Exception as exc:
        await persistence.update_validation_run(
            run_id,
            {
                "status": "failed",
                "completed_cases": completed,
                "error_message": f"{type(exc).__name__}: {exc}",
                "finished_at": datetime.utcnow().isoformat() + "Z",
            },
        )
        raise HTTPException(
            status_code=502,
            detail=f"Validation benchmark failed: {type(exc).__name__}: {exc}",
        ) from exc
