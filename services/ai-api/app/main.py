from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Annotated

import httpx
import os
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

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

PUBLIC_REDISCOVERY_CASES = {
    "abu_mena": {
        "name": "Abu Mena",
        "lat": 30.835833333,
        "lon": 29.666666667,
        "radius_m": 1200,
        "source": "UNESCO World Heritage Centre property 90",
        "split": "calibration",
    },
    "memphis": {
        "name": "Site of Memphis",
        "lat": 29.855500000,
        "lon": 31.261055556,
        "radius_m": 1500,
        "source": "UNESCO World Heritage Centre component 86-001",
        "split": "calibration",
    },
    "karnak": {
        "name": "Temple of Karnak",
        "lat": 25.718888889,
        "lon": 32.657250000,
        "radius_m": 900,
        "source": "UNESCO World Heritage Centre component 087-001",
        "split": "validation",
    },
    "luxor": {
        "name": "Temple of Luxor",
        "lat": 25.699861111,
        "lon": 32.639027778,
        "radius_m": 600,
        "source": "UNESCO World Heritage Centre component 087-002",
        "split": "validation",
    },
    "thebes_necropolis": {
        "name": "Ancient Thebes Necropolis",
        "lat": 25.731444444,
        "lon": 32.597027778,
        "radius_m": 3000,
        "source": "UNESCO World Heritage Centre component 087-003",
        "split": "validation",
    }
}

PUBLIC_FORBIDDEN_CENTERS = [
    (30.835833333, 29.666666667, 1200),
    (29.976040000, 31.130410000, 6000),
    (29.855500000, 31.261055556, 1500),
    (25.718888889, 32.657250000, 900),
    (25.699861111, 32.639027778, 600),
    (25.731444444, 32.597027778, 3000),
]


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
    from app.providers.stac import search_sentinel2

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
    from app.pipeline.raster_baseline import analyze_scene
    from app.providers.stac import search_open_cog_sentinel2

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
    from app.persistence import PersistenceUnavailable, SupabasePersistence
    from app.pipeline.multisensor import analyze_sar_stack, analyze_terrain, fuse_sensor_scores
    from app.pipeline.raster_baseline import analyze_scene
    from app.providers.stac import (
        search_copernicus_dem,
        search_open_cog_sentinel2,
        search_sentinel1_rtc,
    )

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
    from app.persistence import PersistenceUnavailable, SupabasePersistence
    from app.validation.benchmark import (
        ValidationCase,
        compute_benchmark_metrics,
        deterministic_control,
    )
    from app.validation.runner import run_validation_case

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



@app.post("/v1/analysis/hyperfusion-v0")
async def run_hyperfusion_proxy(
    payload: dict,
    authorization: Annotated[str | None, Header()] = None,
) -> dict:
    if os.getenv("SERVERLESS_RASTER_MODE", "false").lower() == "true":
        from app.pipeline.serverless_raster import run_serverless_hyperfusion

        try:
            bbox = tuple(float(value) for value in payload["bbox"])
            start = datetime.fromisoformat(str(payload["start"]).replace("Z", "+00:00"))
            end = datetime.fromisoformat(str(payload["end"]).replace("Z", "+00:00"))
            max_cloud = float(payload.get("max_cloud", 20.0))
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=f"Invalid HyperFusion payload: {exc}") from exc

        validate_bbox(bbox)
        try:
            result = await run_serverless_hyperfusion(
                bbox,
                start,
                end,
                max_cloud=max_cloud,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Serverless HyperFusion failed: {type(exc).__name__}: {exc}",
            ) from exc

        result["persisted"] = False
        result["persistence_note"] = (
            "Serverless preview executes real EO inference without service-role persistence."
        )
        return result

    from app.routes.advanced import HyperFusionRequest, run_hyperfusion_v0

    return await run_hyperfusion_v0(
        HyperFusionRequest(**payload),
        authorization,
    )


@app.get("/v1/foundation/models")
async def list_foundation_models_proxy() -> dict:
    from app.foundation.registry import get_model_registry

    return {
        "models": get_model_registry(),
        "note": (
            "available=true means a real inference path is configured, either a verified "
            "remote endpoint or a supported public embedding source. Synthetic readiness is rejected."
        ),
    }


@app.post("/v1/tournament/run")
async def run_tournament_proxy(
    payload: dict,
    authorization: Annotated[str | None, Header()] = None,
) -> dict:
    from app.routes.tournament import TournamentRequest, run_model_tournament

    return await run_model_tournament(
        TournamentRequest(**payload),
        authorization,
    )


@app.post("/v1/foundation/segment-candidates")
async def segment_candidates_proxy(payload: dict) -> dict:
    from app.routes.geometry import GeometryRequest, segment_candidates

    return await segment_candidates(GeometryRequest(**payload))



@app.get("/v1/smoke/spectral")
async def smoke_spectral() -> dict:
    if os.getenv("ENABLE_PUBLIC_SMOKE_TESTS", "false").lower() != "true":
        raise HTTPException(status_code=404, detail="Smoke tests are disabled.")

    from datetime import timedelta, timezone

    bbox = (31.20, 29.92, 31.22, 29.94)
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=180)

    try:
        from app.pipeline.serverless_raster import (
            analyze_sentinel2_serverless,
            search_pc_sentinel2,
        )
    except Exception as exc:
        return {
            "real_data": False,
            "status": "failed",
            "stage": "import_serverless_pipeline",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }

    try:
        scenes = await search_pc_sentinel2(
            bbox,
            start,
            end,
            max_cloud=25.0,
            limit=3,
        )
    except Exception as exc:
        return {
            "real_data": False,
            "status": "failed",
            "stage": "pc_stac_search",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }

    if not scenes:
        return {
            "real_data": True,
            "status": "inconclusive",
            "stage": "pc_stac_search",
            "reason": "No suitable Sentinel-2 scene found in smoke-test window.",
        }

    scene = min(
        scenes,
        key=lambda item: item.cloud_cover if item.cloud_cover is not None else 101.0,
    )

    try:
        result = await analyze_sentinel2_serverless(scene, bbox)
    except Exception as exc:
        return {
            "real_data": True,
            "status": "failed",
            "stage": "pc_numpy_tile_analysis",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "scene": {
                "id": scene.item_id,
                "datetime": scene.datetime,
                "cloud_cover": scene.cloud_cover,
                "collection": scene.collection,
            },
        }

    return {
        "real_data": True,
        "status": "completed",
        "source": "microsoft-planetary-computer-titiler",
        "scene": {
            "id": scene.item_id,
            "datetime": scene.datetime,
            "cloud_cover": scene.cloud_cover,
        },
        "summary": result["summary"],
        "scientific_note": (
            "Infrastructure smoke test using real Sentinel-2 pixels delivered as a "
            "Planetary Computer NumpyTile. The anomaly rank is not archaeological probability."
        ),
    }




@app.get("/v1/smoke/hyperfusion")
async def smoke_hyperfusion() -> dict:
    if os.getenv("ENABLE_PUBLIC_SMOKE_TESTS", "false").lower() != "true":
        raise HTTPException(status_code=404, detail="Smoke tests are disabled.")

    from datetime import timedelta, timezone

    try:
        from app.pipeline.serverless_raster import run_serverless_hyperfusion
    except Exception as exc:
        return {
            "real_data": False,
            "status": "failed",
            "stage": "import_serverless_hyperfusion",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }

    bbox = (31.20, 29.92, 31.22, 29.94)
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=180)

    try:
        result = await run_serverless_hyperfusion(
            bbox,
            start,
            end,
            max_cloud=25.0,
        )
    except Exception as exc:
        return {
            "real_data": False,
            "status": "failed",
            "stage": "serverless_hyperfusion",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }

    return {
        "real_data": True,
        "status": "completed",
        "engine": result["engine"],
        "per_branch_evaluation": per_branch_evaluation,
        "branches_used": result["branches_used"],
        "branch_summaries": result["branches"],
        "fusion_summary": result["fusion"]["summary"],
        "discovery": result["discovery"],
        "limitations": result["limitations"],
        "discovery_errors": result["discovery_errors"],
        "scientific_status": result["scientific_status"],
    }



@app.get("/v1/smoke/dem")
async def smoke_dem() -> dict:
    if os.getenv("ENABLE_PUBLIC_SMOKE_TESTS", "false").lower() != "true":
        raise HTTPException(status_code=404, detail="Smoke tests are disabled.")

    import numpy as np
    from app.pipeline.serverless_raster import (
        read_pc_item_assets_npy,
        search_pc_collection,
    )

    bbox = (31.20, 29.92, 31.22, 29.94)
    try:
        scenes = await search_pc_collection(
            "cop-dem-glo-30",
            bbox,
            None,
            None,
            None,
            4,
        )
    except Exception as exc:
        return {
            "status": "failed",
            "stage": "dem_search",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }

    if not scenes:
        return {"status": "inconclusive", "reason": "No DEM scene found."}

    reports = []
    for scene in scenes:
        report = {
            "item_id": scene.item_id,
            "assets": list(scene.assets),
        }
        if "data" not in scene.assets:
            report["error"] = "data asset missing"
            reports.append(report)
            continue
        try:
            cube, mask = await read_pc_item_assets_npy(
                scene,
                bbox,
                ("data",),
                width=192,
                height=192,
                return_mask=False,
            )
            raw = cube[0]
            finite = raw[np.isfinite(raw)]
            report.update(
                {
                    "shape": list(cube.shape),
                    "mask_valid": int(mask.sum()),
                    "finite_count": int(finite.size),
                    "min": float(np.min(finite)) if finite.size else None,
                    "max": float(np.max(finite)) if finite.size else None,
                    "mean": float(np.mean(finite)) if finite.size else None,
                    "zero_count": int(np.sum(finite == 0)) if finite.size else 0,
                    "lt_minus_1000": int(np.sum(finite <= -1000)) if finite.size else 0,
                }
            )
        except Exception as exc:
            report.update(
                {
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )
        reports.append(report)

    return {
        "status": "completed",
        "real_data": True,
        "reports": reports,
    }



@app.get("/v1/smoke/olmoearth")
async def smoke_olmoearth() -> dict:
    if os.getenv("ENABLE_PUBLIC_SMOKE_TESTS", "false").lower() != "true":
        raise HTTPException(status_code=404, detail="Smoke tests are disabled.")

    bbox = (31.20, 29.92, 31.22, 29.94)
    try:
        from app.pipeline.olmoearth_embeddings import analyze_olmoearth_embeddings
    except Exception as exc:
        return {
            "real_data": False,
            "status": "failed",
            "stage": "import_olmoearth",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }

    try:
        result = await analyze_olmoearth_embeddings(
            bbox,
            year=2025,
            dims=64,
            target_shape=(192, 192),
        )
    except Exception as exc:
        return {
            "real_data": False,
            "status": "failed",
            "stage": "olmoearth_embedding_read",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }

    return {
        "real_data": True,
        "status": "completed",
        "source": "Source Cooperative / Ai2",
        "summary": result["summary"],
        "provenance": result["provenance"],
        "scientific_note": (
            "OlmoEarth v1.3 annual foundation-model embedding anomaly rank. "
            "It is not an archaeological probability and uses the 2025 annual embedding."
        ),
    }



@app.get("/v1/smoke/rediscovery/{case_key}")
async def smoke_rediscovery(case_key: str, control: bool = False, profile: str = "equal_v0") -> dict:
    if os.getenv("ENABLE_PUBLIC_SMOKE_TESTS", "false").lower() != "true":
        raise HTTPException(status_code=404, detail="Smoke tests are disabled.")

    from datetime import timedelta, timezone

    from app.pipeline.serverless_raster import run_serverless_hyperfusion
    from app.validation.benchmark import (
        ValidationCase,
        bbox_from_center,
        deterministic_control,
        evaluate_heatmap,
    )

    if profile not in {"equal_v0", "calibration_v1"}:
        raise HTTPException(status_code=422, detail=f"Unknown fusion profile: {profile}")

    fixture = PUBLIC_REDISCOVERY_CASES.get(case_key)
    if fixture is None:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown public rediscovery case: {case_key}",
        )

    positive = ValidationCase(
        name=fixture["name"],
        case_type="positive",
        lat=float(fixture["lat"]),
        lon=float(fixture["lon"]),
        radius_m=int(fixture["radius_m"]),
        site_id=None,
    )
    case = (
        deterministic_control(
            positive.lat,
            positive.lon,
            positive.radius_m,
            0 if case_key == "karnak" else 1,
            forbidden_centers=PUBLIC_FORBIDDEN_CENTERS,
        )
        if control
        else positive
    )

    bbox = bbox_from_center(case.lat, case.lon, case.radius_m)
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=730)

    try:
        result = await run_serverless_hyperfusion(
            bbox,
            start,
            end,
            max_cloud=25.0,
            include_branch_heatmaps=True,
            fusion_profile=profile,
        )
        evaluation = evaluate_heatmap(
            result["fusion"]["heatmap"],
            center_lat=case.lat,
            center_lon=case.lon,
            radius_m=case.radius_m,
        )
    except Exception as exc:
        return {
            "real_data": False,
            "status": "failed",
            "case_key": case_key,
            "case_type": case.case_type,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }

    per_branch_evaluation = {}
    for branch_name, heatmap in (result.get("branch_heatmaps") or {}).items():
        branch_eval = evaluate_heatmap(
            heatmap,
            center_lat=case.lat,
            center_lon=case.lon,
            radius_m=case.radius_m,
        )
        per_branch_evaluation[branch_name] = {
            "rediscovery_score": branch_eval.rediscovery_score,
            "center_block_percentile": branch_eval.center_block_percentile,
            "concentration_ratio": branch_eval.concentration_ratio,
            "detected_high_priority": branch_eval.detected_high_priority,
        }

    return {
        "real_data": True,
        "status": "completed",
        "case_key": case_key,
        "case_name": case.name,
        "case_type": case.case_type,
        "fusion_profile": profile,
        "public_source": fixture["source"] if not control else None,
        "split": fixture.get("split"),
        "evaluation": {
            "rediscovery_score": evaluation.rediscovery_score,
            "center_block_percentile": evaluation.center_block_percentile,
            "concentration_ratio": evaluation.concentration_ratio,
            "detected_high_priority": evaluation.detected_high_priority,
        },
        "per_branch_evaluation": per_branch_evaluation,
        "branches_used": result["branches_used"],
        "fusion_summary": result["fusion"]["summary"],
        "limitations": result["limitations"],
        "scientific_note": (
            "Known-site case uses a public UNESCO component coordinate. "
            "Control cases are unlabeled comparisons, not confirmed archaeological negatives. "
            "Rediscovery score is a ranking metric, not an archaeological probability."
        ),
    }
