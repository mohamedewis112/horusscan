from __future__ import annotations

from datetime import datetime
from typing import Any

from app.pipeline.serverless_raster import run_serverless_hyperfusion
from app.validation.benchmark import ValidationCase, bbox_from_center, evaluate_heatmap


async def run_validation_case(
    case: ValidationCase,
    start: datetime,
    end: datetime,
    max_cloud: float,
    fusion_profile: str = "calibration_v1",
) -> dict[str, Any]:
    bbox = bbox_from_center(case.lat, case.lon, case.radius_m)

    result = await run_serverless_hyperfusion(
        bbox,
        start,
        end,
        max_cloud=max_cloud,
        include_branch_heatmaps=False,
        fusion_profile=fusion_profile,
    )

    evaluation = evaluate_heatmap(
        result["fusion"]["heatmap"],
        center_lat=case.lat,
        center_lon=case.lon,
        radius_m=case.radius_m,
    )

    return {
        "evaluation": evaluation,
        "fusion_summary": result["fusion"]["summary"],
        "sensor_summary": {
            "branches_used": result["branches_used"],
            "branches": result["branches"],
            "limitations": result["limitations"],
            "discovery": result["discovery"],
            "provenance": result["provenance"],
            "engine": result["engine"],
            "fusion_profile": fusion_profile,
        },
    }
