from __future__ import annotations

from datetime import datetime
import os
from typing import Any

import numpy as np
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.foundation.extensions import heatmap_to_grid, load_available_external_models
from app.foundation.registry import get_model_registry
from app.pipeline.council import build_council_report
from app.pipeline.serverless_raster import (
    fuse_serverless_layers,
    run_serverless_hyperfusion,
    search_pc_collection,
)
from app.pipeline.physics_sar import analyze_physics_sar


router = APIRouter(prefix="/v1/council", tags=["horus-council"])


class CouncilRequest(BaseModel):
    bbox: tuple[float, float, float, float]
    start: datetime
    end: datetime
    max_cloud: float = Field(default=20.0, ge=0.0, le=100.0)
    fusion_profile: str = "calibration_v1"
    max_candidates: int = Field(default=10, ge=1, le=15)


def _validate_bbox(bbox: tuple[float, float, float, float]) -> None:
    min_lon, min_lat, max_lon, max_lat = bbox
    if min_lon >= max_lon or min_lat >= max_lat:
        raise HTTPException(status_code=422, detail="Invalid bounding box.")


@router.post("/analyze")
async def analyze_with_horus_council(request: CouncilRequest) -> dict[str, Any]:
    _validate_bbox(request.bbox)
    if request.fusion_profile not in {"equal_v0", "calibration_v1"}:
        raise HTTPException(status_code=422, detail="Unknown fusion profile.")

    try:
        base = await run_serverless_hyperfusion(
            request.bbox,
            request.start,
            request.end,
            max_cloud=request.max_cloud,
            include_branch_heatmaps=True,
            fusion_profile=request.fusion_profile,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Core HyperFusion failed: {type(exc).__name__}: {exc}",
        ) from exc

    shape = (192, 192)
    evidence: dict[str, np.ndarray] = {}
    branch_heatmaps = base.get("branch_heatmaps") or {}
    for name, heatmap in branch_heatmaps.items():
        if isinstance(heatmap, dict):
            grid = heatmap_to_grid(heatmap, request.bbox, shape)
            if np.isfinite(grid).sum() >= 64:
                evidence[name] = grid

    external_layers, external_summaries, external_provenance, external_limitations = (
        await load_available_external_models(
            request.bbox,
            request.start,
            request.end,
            shape,
        )
    )
    evidence.update(external_layers)

    branches = dict(base.get("branches") or {})
    branches.update(external_summaries)
    provenance = dict(base.get("provenance") or {})
    provenance.update(external_provenance)
    limitations = dict(base.get("limitations") or {})
    limitations.update(external_limitations)

    if not evidence:
        raise HTTPException(status_code=502, detail="Council received no usable evidence layers.")

    fusion_evidence = dict(evidence)
    fusion = fuse_serverless_layers(
        fusion_evidence,
        request.bbox,
        profile=request.fusion_profile,
    )

    # Physics-SAR participates in adjudication but does not alter the calibrated
    # candidate surface until it receives its own calibration profile.
    if os.getenv("PHYSICS_SAR_SERVERLESS_ENABLED", "false").lower() == "true":
        try:
            sar_scenes = await search_pc_collection(
                "sentinel-1-rtc",
                request.bbox,
                request.start,
                request.end,
                None,
                6,
            )
            physics_sar = await analyze_physics_sar(sar_scenes, request.bbox)
            evidence["physics_sar"] = physics_sar["score"]
            branches["physics_sar"] = physics_sar["summary"]
            provenance["physics_sar"] = physics_sar["provenance"]
        except Exception as exc:
            limitations["physics_sar"] = f"{type(exc).__name__}: {exc}"

    council = build_council_report(
        evidence=evidence,
        fused=fusion["score"],
        bbox=request.bbox,
        fusion_profile=request.fusion_profile,
        limitations=limitations,
        max_candidates=request.max_candidates,
    )

    registry = get_model_registry()
    active_keys = set(evidence)
    model_states = []
    for row in registry:
        key = str(row["key"])
        participates = key in active_keys or (
            key == "olmoearth" and any(name.startswith("olmoearth_") for name in active_keys)
        )
        model_states.append(
            {
                "key": key,
                "role": row["role"],
                "available": bool(row.get("available")),
                "participates_in_this_run": participates,
                "inference_mode": row.get("inference_mode"),
                "reason": row.get("reason"),
            }
        )

    return {
        "real_data": True,
        "engine": "horus-council-v1",
        "fusion_profile": request.fusion_profile,
        "branches_used": list(evidence),
        "fusion_branches_used": list(fusion_evidence),
        "adjudication_only_branches": [
            name for name in evidence if name not in fusion_evidence
        ],
        "branches": branches,
        "provenance": provenance,
        "limitations": limitations,
        "fusion": {
            "summary": fusion["summary"],
            "heatmap": fusion["heatmap"],
            "provenance": fusion["provenance"],
        },
        "council": council,
        "model_states": model_states,
        "discovery": base.get("discovery") or {},
        "scientific_status": (
            "Horus Council combines independent evidence families and available real model outputs. "
            "The confidence index is a calibrated ranking index with uncertainty, not a probability "
            "that archaeology is present."
        ),
    }


@router.get("/smoke")
async def smoke_council() -> dict[str, Any]:
    request = CouncilRequest(
        bbox=(31.20, 29.92, 31.22, 29.94),
        start=datetime.fromisoformat("2026-04-01T00:00:00+00:00"),
        end=datetime.fromisoformat("2026-10-26T23:59:59+00:00"),
        max_cloud=25.0,
        fusion_profile="calibration_v1",
        max_candidates=3,
    )
    result = await analyze_with_horus_council(request)
    return {
        "status": "completed",
        "real_data": result["real_data"],
        "engine": result["engine"],
        "branches_used": result["branches_used"],
        "candidate_count": result["council"]["candidate_count"],
        "candidate_summary": [
            {
                "candidate_id": item["candidate_id"],
                "priority": item["priority"],
                "evidence_confidence_index": item["evidence_confidence_index"],
                "uncertainty_band": item["uncertainty_band"],
                "independent_support_count": item["independent_support_count"],
                "model_agreement": item["council_summary"]["model_agreement"],
                "critic_verdict": item["critic"]["verdict"],
            }
            for item in result["council"]["candidates"]
        ],
        "model_states": result["model_states"],
        "limitations": list(result["limitations"].keys()),
        "scientific_status": result["scientific_status"],
    }
