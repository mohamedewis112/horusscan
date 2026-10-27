from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from app.pipeline.multisensor import analyze_sar_stack, analyze_terrain, fuse_sensor_scores
from app.pipeline.raster_baseline import analyze_scene
from app.providers.stac import (
    search_copernicus_dem,
    search_open_cog_sentinel2,
    search_sentinel1_rtc,
)
from app.validation.benchmark import ValidationCase, bbox_from_center, evaluate_heatmap


async def run_validation_case(
    case: ValidationCase,
    start: datetime,
    end: datetime,
    max_cloud: float,
) -> dict[str, Any]:
    bbox = bbox_from_center(case.lat, case.lon, case.radius_m)

    s2_task = search_open_cog_sentinel2(
        bbox,
        start,
        end,
        max_cloud=max_cloud,
        limit=5,
    )
    s1_task = asyncio.to_thread(search_sentinel1_rtc, bbox, start, end, 6)
    dem_task = asyncio.to_thread(search_copernicus_dem, bbox, 8)
    s2_scenes, s1_scenes, dem_scenes = await asyncio.gather(s2_task, s1_task, dem_task)

    if not s2_scenes:
        raise ValueError("No suitable Sentinel-2 observation for validation case.")

    s2_scene = min(
        s2_scenes,
        key=lambda item: item.cloud_cover if item.cloud_cover is not None else 101.0,
    )

    spectral = await asyncio.to_thread(
        analyze_scene,
        s2_scene.item_id,
        s2_scene.cloud_cover,
        s2_scene.assets,
        bbox,
    )

    sar = None
    sar_error = None
    if s1_scenes:
        try:
            sar = await asyncio.to_thread(analyze_sar_stack, s1_scenes, bbox)
        except (ValueError, KeyError, OSError) as exc:
            sar_error = str(exc)

    terrain = None
    terrain_error = None
    if dem_scenes:
        try:
            terrain = await asyncio.to_thread(analyze_terrain, dem_scenes, bbox)
        except (ValueError, KeyError, OSError) as exc:
            terrain_error = str(exc)

    fusion = await asyncio.to_thread(
        fuse_sensor_scores,
        spectral.score,
        sar.score if sar else None,
        terrain.score if terrain else None,
        bbox,
    )

    evaluation = evaluate_heatmap(
        fusion.heatmap,
        center_lat=case.lat,
        center_lon=case.lon,
        radius_m=case.radius_m,
    )

    return {
        "evaluation": evaluation,
        "fusion_summary": fusion.summary,
        "sensor_summary": {
            "spectral": spectral.summary,
            "sar": sar.summary if sar else None,
            "terrain": terrain.summary if terrain else None,
            "limitations": {
                "sar": sar_error,
                "terrain": terrain_error,
            },
            "sources": {
                "sentinel2": {
                    "provider": s2_scene.provider,
                    "item_id": s2_scene.item_id,
                    "datetime": s2_scene.datetime,
                },
                "sentinel1_count": len(s1_scenes),
                "dem_tile_count": len(dem_scenes),
            },
        },
    }
