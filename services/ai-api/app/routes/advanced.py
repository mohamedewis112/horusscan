from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Annotated

import httpx
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from app.persistence import PersistenceUnavailable, SupabasePersistence
from app.pipeline.advanced_sensors import analyze_hyperspectral_scene, analyze_thermal_stack
from app.pipeline.multisensor import analyze_sar_stack, analyze_terrain, fuse_evidence_layers
from app.pipeline.raster_baseline import analyze_scene
from app.providers.advanced_sensors import (
    search_emit_l2a,
    search_enmap_l2a,
    search_landsat_thermal,
)
from app.providers.stac import (
    search_copernicus_dem,
    search_open_cog_sentinel2,
    search_sentinel1_rtc,
)


router = APIRouter(prefix="/v1/analysis", tags=["advanced-analysis"])


class HyperFusionRequest(BaseModel):
    bbox: tuple[float, float, float, float]
    start: datetime
    end: datetime
    max_cloud: float = Field(default=20.0, ge=0.0, le=100.0)
    project_id: str | None = None
    aoi_id: str | None = None
    analysis_run_id: str | None = None


def _validate_bbox(bbox: tuple[float, float, float, float]) -> None:
    min_lon, min_lat, max_lon, max_lat = bbox
    if min_lon >= max_lon or min_lat >= max_lat:
        raise HTTPException(status_code=422, detail="Invalid bounding box.")


def _bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Invalid Authorization header.")
    return token


@router.post("/hyperfusion-v0")
async def run_hyperfusion_v0(
    request: HyperFusionRequest,
    authorization: Annotated[str | None, Header()] = None,
) -> dict:
    _validate_bbox(request.bbox)

    persistence: SupabasePersistence | None = None
    persist_requested = all(
        [request.project_id, request.aoi_id, request.analysis_run_id, authorization]
    )
    if persist_requested:
        try:
            persistence = SupabasePersistence()
            token = _bearer(authorization)
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
                    "progress_stage": "advanced_sensor_discovery",
                    "started_at": datetime.utcnow().isoformat() + "Z",
                },
            )
        except (PersistenceUnavailable, httpx.HTTPError, PermissionError) as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

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
    enmap_task = search_enmap_l2a(request.bbox, request.start, request.end, 4)
    thermal_task = asyncio.to_thread(
        search_landsat_thermal,
        request.bbox,
        request.start,
        request.end,
        8,
    )
    emit_task = search_emit_l2a(request.bbox, request.start, request.end, 4)

    results = await asyncio.gather(
        s2_task,
        s1_task,
        dem_task,
        enmap_task,
        thermal_task,
        emit_task,
        return_exceptions=True,
    )

    s2_scenes = [] if isinstance(results[0], Exception) else results[0]
    s1_scenes = [] if isinstance(results[1], Exception) else results[1]
    dem_scenes = [] if isinstance(results[2], Exception) else results[2]
    enmap_scenes = [] if isinstance(results[3], Exception) else results[3]
    thermal_scenes = [] if isinstance(results[4], Exception) else results[4]
    emit_scenes = [] if isinstance(results[5], Exception) else results[5]

    discovery_errors = {
        "sentinel2": str(results[0]) if isinstance(results[0], Exception) else None,
        "sentinel1": str(results[1]) if isinstance(results[1], Exception) else None,
        "dem": str(results[2]) if isinstance(results[2], Exception) else None,
        "enmap": str(results[3]) if isinstance(results[3], Exception) else None,
        "landsat_thermal": str(results[4]) if isinstance(results[4], Exception) else None,
        "emit": str(results[5]) if isinstance(results[5], Exception) else None,
    }

    if not s2_scenes:
        if persistence and request.analysis_run_id:
            await persistence.update_run(
                request.analysis_run_id,
                {
                    "status": "inconclusive",
                    "progress_stage": "insufficient_core_data",
                    "error_message": "No suitable Sentinel-2 observation.",
                    "finished_at": datetime.utcnow().isoformat() + "Z",
                },
            )
        raise HTTPException(
            status_code=404,
            detail="No suitable Sentinel-2 observation; core HyperFusion cannot run.",
        )

    if persistence and request.analysis_run_id:
        await persistence.update_run(
            request.analysis_run_id,
            {"status": "preprocessing", "progress_stage": "advanced_sensor_preprocessing"},
        )

    evidence = {}
    branches = {}
    limitations = {}
    layer_payloads = []

    sar = None
    terrain = None
    hyperspectral_result = None
    thermal = None

    s2_scene = min(
        s2_scenes,
        key=lambda item: item.cloud_cover if item.cloud_cover is not None else 101.0,
    )
    spectral = await asyncio.to_thread(
        analyze_scene,
        s2_scene.item_id,
        s2_scene.cloud_cover,
        s2_scene.assets,
        request.bbox,
    )
    evidence["spectral"] = spectral.score
    branches["spectral"] = spectral.summary
    layer_payloads.append(
        (
            "spectral_anomaly",
            spectral.summary,
            {
                "provider": s2_scene.provider,
                "collection": s2_scene.collection,
                "item_id": s2_scene.item_id,
                "datetime": s2_scene.datetime,
                "bands": ["B02", "B04", "B08", "B11"],
            },
            spectral.heatmap,
        )
    )

    if s1_scenes:
        try:
            sar = await asyncio.to_thread(analyze_sar_stack, s1_scenes, request.bbox)
            evidence["sar"] = sar.score
            branches["sar"] = sar.summary
            layer_payloads.append(("sar_anomaly", sar.summary, sar.provenance, sar.heatmap))
        except Exception as exc:
            limitations["sar"] = f"{type(exc).__name__}: {exc}"

    if dem_scenes:
        try:
            terrain = await asyncio.to_thread(analyze_terrain, dem_scenes, request.bbox)
            evidence["terrain"] = terrain.score
            branches["terrain"] = terrain.summary
            layer_payloads.append(
                ("terrain_anomaly", terrain.summary, terrain.provenance, terrain.heatmap)
            )
        except Exception as exc:
            limitations["terrain"] = f"{type(exc).__name__}: {exc}"

    if enmap_scenes:
        hyperspectral_errors = []
        for scene in enmap_scenes:
            try:
                hyperspectral_result = await asyncio.to_thread(
                    analyze_hyperspectral_scene,
                    scene,
                    request.bbox,
                )
                break
            except Exception as exc:
                hyperspectral_errors.append(f"{scene.item_id}: {type(exc).__name__}: {exc}")
        if hyperspectral_result:
            evidence["hyperspectral"] = hyperspectral_result.score
            branches["hyperspectral"] = hyperspectral_result.summary
            layer_payloads.append(
                (
                    "hyperspectral_anomaly",
                    hyperspectral_result.summary,
                    hyperspectral_result.provenance,
                    hyperspectral_result.heatmap,
                )
            )
        elif hyperspectral_errors:
            limitations["hyperspectral"] = hyperspectral_errors[:3]

    if thermal_scenes:
        try:
            thermal = await asyncio.to_thread(
                analyze_thermal_stack,
                thermal_scenes,
                request.bbox,
            )
            evidence["thermal"] = thermal.score
            branches["thermal"] = thermal.summary
            layer_payloads.append(
                ("thermal_persistence_anomaly", thermal.summary, thermal.provenance, thermal.heatmap)
            )
        except Exception as exc:
            limitations["thermal"] = f"{type(exc).__name__}: {exc}"

    if persistence and request.analysis_run_id:
        await persistence.update_run(
            request.analysis_run_id,
            {"status": "fusing", "progress_stage": "hyperfusion_v0"},
        )

    fusion = await asyncio.to_thread(
        fuse_evidence_layers,
        evidence,
        request.bbox,
    )
    layer_payloads.append(
        (
            "horus_hyperfusion_v0",
            fusion.summary,
            fusion.provenance,
            fusion.heatmap,
        )
    )

    if persistence and request.analysis_run_id:
        scene_records = []
        scene_records.append((s2_scene, "spectral", 0))
        scene_records.extend((scene, "sar", idx) for idx, scene in enumerate(s1_scenes))
        scene_records.extend((scene, "terrain", idx) for idx, scene in enumerate(dem_scenes))
        scene_records.extend((scene, "hyperspectral", idx) for idx, scene in enumerate(enmap_scenes))
        scene_records.extend((scene, "thermal", idx) for idx, scene in enumerate(thermal_scenes))

        for scene, role, ordinal in scene_records:
            scene_id = await persistence.store_scene(scene, role)
            await persistence.link_scene(
                request.analysis_run_id,
                scene_id,
                role,
                ordinal,
            )

        for layer_type, stats, provenance, data in layer_payloads:
            await persistence.store_layer(
                request.analysis_run_id,
                layer_type,
                stats,
                provenance,
                data,
            )

        quality = min(1.0, 0.35 + 0.13 * len(evidence))
        await persistence.update_run(
            request.analysis_run_id,
            {
                "status": "completed",
                "progress_stage": "complete",
                "data_quality_score": float(quality),
                "finished_at": datetime.utcnow().isoformat() + "Z",
            },
        )

    return {
        "real_data": True,
        "persisted": bool(persistence and request.analysis_run_id),
        "engine": "horus-hyperfusion-v0",
        "branches_used": list(evidence),
        "branches": branches,
        "fusion": {
            "summary": fusion.summary,
            "heatmap": fusion.heatmap,
            "provenance": fusion.provenance,
        },
        "discovery": {
            "sentinel2_count": len(s2_scenes),
            "sentinel1_count": len(s1_scenes),
            "dem_count": len(dem_scenes),
            "enmap_count": len(enmap_scenes),
            "landsat_thermal_count": len(thermal_scenes),
            "emit_count": len(emit_scenes),
            "emit_note": (
                "EMIT discovery is active. Download/inference requires Earthdata-authorized "
                "data access and is not silently substituted with synthetic data."
            ),
        },
        "limitations": limitations,
        "discovery_errors": discovery_errors,
        "scientific_status": (
            "Evidence fusion across available real optical, radar, terrain, hyperspectral and "
            "thermal observations. Thermal branch is persistence analysis, not true day/night "
            "thermal inertia. The fused rank is not a calibrated probability and does not confirm "
            "a subsurface structure."
        ),
    }
