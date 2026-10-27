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
    score_to_heatmap,
)
from app.pipeline.physics_sar import analyze_physics_sar
from app.pipeline.archaeology_memory import (
    center_mean_from_heatmap,
    review_candidate_with_archaeology_memory,
)
from app.pipeline.reference_bank import REFERENCE_FINGERPRINTS, REFERENCE_SITES


router = APIRouter(prefix="/v1/council", tags=["horus-council"])


class CouncilRequest(BaseModel):
    bbox: tuple[float, float, float, float]
    aoi_polygon: list[tuple[float, float]] | None = None
    start: datetime
    end: datetime
    max_cloud: float = Field(default=20.0, ge=0.0, le=100.0)
    fusion_profile: str = "calibration_v1"
    max_candidates: int = Field(default=10, ge=1, le=15)


def _validate_bbox(bbox: tuple[float, float, float, float]) -> None:
    min_lon, min_lat, max_lon, max_lat = bbox
    if min_lon >= max_lon or min_lat >= max_lat:
        raise HTTPException(status_code=422, detail="Invalid bounding box.")


def _validate_polygon(
    polygon: list[tuple[float, float]] | None,
    bbox: tuple[float, float, float, float],
) -> None:
    if polygon is None:
        return
    if len(polygon) < 3:
        raise HTTPException(status_code=422, detail="AOI polygon needs at least three points.")
    min_lon, min_lat, max_lon, max_lat = bbox
    for lon, lat in polygon:
        if not (min_lon - 1e-8 <= lon <= max_lon + 1e-8 and min_lat - 1e-8 <= lat <= max_lat + 1e-8):
            raise HTTPException(status_code=422, detail="AOI polygon point falls outside its bounding box.")


def _polygon_mask(
    shape: tuple[int, int],
    bbox: tuple[float, float, float, float],
    polygon: list[tuple[float, float]] | None,
) -> np.ndarray:
    if not polygon:
        return np.ones(shape, dtype=bool)

    min_lon, min_lat, max_lon, max_lat = bbox
    height, width = shape
    mask = np.zeros(shape, dtype=bool)
    vertices = [(float(lon), float(lat)) for lon, lat in polygon]

    for row in range(height):
        lat = max_lat - ((row + 0.5) / height) * (max_lat - min_lat)
        for col in range(width):
            lon = min_lon + ((col + 0.5) / width) * (max_lon - min_lon)
            inside = False
            j = len(vertices) - 1
            for i, (xi, yi) in enumerate(vertices):
                xj, yj = vertices[j]
                intersects = ((yi > lat) != (yj > lat)) and (
                    lon < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi
                )
                if intersects:
                    inside = not inside
                j = i
            mask[row, col] = inside

    return mask


@router.post("/analyze")
async def analyze_with_horus_council(request: CouncilRequest) -> dict[str, Any]:
    _validate_bbox(request.bbox)
    _validate_polygon(request.aoi_polygon, request.bbox)
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

    polygon_mask = _polygon_mask(shape, request.bbox, request.aoi_polygon)

    fusion_evidence = {
        name: np.where(polygon_mask, score, np.nan).astype(np.float32)
        for name, score in evidence.items()
    }
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
            evidence["physics_sar"] = np.where(
                polygon_mask,
                physics_sar["score"],
                np.nan,
            ).astype(np.float32)
            branches["physics_sar"] = physics_sar["summary"]
            provenance["physics_sar"] = physics_sar["provenance"]
        except Exception as exc:
            limitations["physics_sar"] = f"{type(exc).__name__}: {exc}"

    council_evidence = {
        name: np.where(polygon_mask, score, np.nan).astype(np.float32)
        for name, score in evidence.items()
    }

    council = build_council_report(
        evidence=council_evidence,
        fused=fusion["score"],
        bbox=request.bbox,
        fusion_profile=request.fusion_profile,
        limitations=limitations,
        max_candidates=request.max_candidates,
    )

    memory_ready = bool(REFERENCE_FINGERPRINTS)
    for candidate in council["candidates"]:
        approx = candidate.get("approximate_center") or {}
        review = review_candidate_with_archaeology_memory(
            candidate.get("branch_scores") or {},
            float(approx.get("lat", 0.0)),
            float(approx.get("lon", 0.0)),
        )
        candidate["archaeology_memory"] = review

        if review.get("status") == "ready":
            candidate["consensus_matrix"].append(
                {
                    "member": "archaeology_memory",
                    "independence_group": "archaeology_reference_memory",
                    "verdict": review["memory_verdict"],
                    "evidence_score": review["memory_similarity_score"],
                    "adjudication_only": True,
                }
            )
            candidate["consensus_matrix"].append(
                {
                    "member": "necropolis_context",
                    "independence_group": "archaeology_reference_memory",
                    "verdict": review["necropolis_verdict"],
                    "evidence_score": review["necropolis_context_score"],
                    "adjudication_only": True,
                }
            )

    council["memory_layer"] = {
        "status": "ready" if memory_ready else "awaiting_reference_fingerprints",
        "reference_site_count": len(REFERENCE_SITES),
        "fingerprint_count": len(REFERENCE_FINGERPRINTS),
        "dataset_version": "egypt-arch-memory-v1-2026-10-27",
        "adjudication_only": True,
        "confidence_adjustment": 0.0,
    }

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
            "heatmap": score_to_heatmap(fusion["score"], request.bbox),
            "provenance": {
                **fusion["provenance"],
                "aoi_mask": "polygon" if request.aoi_polygon else "bbox",
            },
        },
        "council": council,
        "model_states": model_states + [
            {
                "key": "archaeology_memory",
                "role": "reference_fingerprint_retrieval",
                "available": memory_ready,
                "participates_in_this_run": memory_ready,
                "inference_mode": "public_reference_retrieval",
                "reason": None if memory_ready else "Reference fingerprints are not populated yet.",
            },
            {
                "key": "necropolis_context",
                "role": "funerary_context_reviewer",
                "available": memory_ready,
                "participates_in_this_run": memory_ready,
                "inference_mode": "public_necropolis_reference_retrieval",
                "reason": None if memory_ready else "Reference fingerprints are not populated yet.",
            },
        ],
        "discovery": base.get("discovery") or {},
        "aoi": {
            "mode": "polygon" if request.aoi_polygon else "bbox",
            "polygon_vertex_count": len(request.aoi_polygon or []),
        },
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
        max_candidates=10,
    )
    result = await analyze_with_horus_council(request)
    return {
        "status": "completed",
        "real_data": result["real_data"],
        "engine": result["engine"],
        "branches_used": result["branches_used"],
        "candidate_count": result["council"]["candidate_count"],
        "coverage_summary": result["council"].get("coverage_summary"),
        "memory_layer": result["council"].get("memory_layer"),
        "candidate_summary": [
            {
                "candidate_id": item["candidate_id"],
                "priority": item["priority"],
                "evidence_confidence_index": item["evidence_confidence_index"],
                "uncertainty_band": item["uncertainty_band"],
                "independent_support_count": item["independent_support_count"],
                "model_agreement": item["council_summary"]["model_agreement"],
                "critic_verdict": item["critic"]["verdict"],
                "multi_scale_review": item.get("multi_scale_review"),
                "archaeology_memory": item.get("archaeology_memory"),
            }
            for item in result["council"]["candidates"]
        ],
        "model_states": result["model_states"],
        "limitations": list(result["limitations"].keys()),
        "scientific_status": result["scientific_status"],
    }



@router.get("/reference-fingerprint/{slug}")
async def reference_fingerprint(slug: str) -> dict[str, Any]:
    if os.getenv("ENABLE_PUBLIC_SMOKE_TESTS", "false").lower() != "true":
        raise HTTPException(status_code=404, detail="Reference bootstrap is disabled.")

    site = REFERENCE_SITES.get(slug)
    if site is None:
        raise HTTPException(status_code=404, detail="Unknown reference site.")

    lat = float(site["lat"])
    lon = float(site["lon"])
    radius_m = int(site["radius_m"])
    lat_delta = (radius_m * 2.2) / 111_320.0
    lon_delta = (radius_m * 2.2) / (
        111_320.0 * max(np.cos(np.deg2rad(lat)), 0.2)
    )
    bbox = (
        lon - lon_delta,
        lat - lat_delta,
        lon + lon_delta,
        lat + lat_delta,
    )

    result = await run_serverless_hyperfusion(
        bbox,
        datetime.fromisoformat("2024-10-27T00:00:00+00:00"),
        datetime.fromisoformat("2026-10-26T23:59:59+00:00"),
        max_cloud=25.0,
        include_branch_heatmaps=True,
        fusion_profile="calibration_v1",
    )

    fingerprint: dict[str, float] = {}
    for name, heatmap in (result.get("branch_heatmaps") or {}).items():
        if not isinstance(heatmap, dict):
            continue
        base_name = "olmoearth" if name.startswith("olmoearth_") else name
        if base_name not in {"spectral", "sar", "terrain", "thermal", "olmoearth"}:
            continue
        fingerprint[base_name] = round(
            center_mean_from_heatmap(heatmap, lat, lon, radius_m),
            6,
        )

    fusion_center_score = center_mean_from_heatmap(
        result["fusion"]["heatmap"],
        lat,
        lon,
        radius_m,
    )

    return {
        "status": "completed",
        "slug": slug,
        "name": site["name"],
        "archetype": site["archetype"],
        "site_type": site["site_type"],
        "fingerprint": fingerprint,
        "fusion_center_score": round(fusion_center_score, 6),
        "fingerprint_version": "sensor-fingerprint-v1",
        "real_data": True,
        "not_probability": True,
        "provenance": {
            "branches_used": result.get("branches_used"),
            "engine": result.get("engine"),
            "period": ["2024-10-27", "2026-10-26"],
            "public_reference_source": site["source"],
        },
    }
