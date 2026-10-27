from __future__ import annotations

import time
from datetime import datetime
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from app.foundation.client import ModelUnavailable, infer_remote
from app.foundation.registry import MODEL_SPECS, get_model_registry
from app.persistence import PersistenceUnavailable, SupabasePersistence
from app.validation.benchmark import (
    ValidationCase,
    bbox_from_center,
    compute_benchmark_metrics,
    deterministic_control,
    evaluate_heatmap,
)
from app.validation.runner import run_validation_case


router = APIRouter(prefix="/v1", tags=["foundation-models"])


DETECTION_COMPETITORS = {
    "terramind",
    "prithvi_eo_2",
    "dinov3_satellite",
    "spectralearth_fm",
    "terraflow",
    "olmoearth",
    "physics_sar",
}


class TournamentRequest(BaseModel):
    dataset_version: str = "unesco-egypt-public-v1-2026-10-27"
    split: str = "validation"
    start: datetime
    end: datetime
    max_cloud: float = Field(default=20.0, ge=0.0, le=100.0)
    competitors: list[str] | None = None
    include_baseline: bool = True
    include_unlabeled_controls: bool = True


def _bearer(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required.")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Invalid Authorization header.")
    return token


def _rank_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = []
    for entry in entries:
        metrics = entry.get("metrics", {})
        if entry.get("status") != "completed":
            composite = None
        else:
            rediscovery = float(metrics.get("known_site_rediscovery_rate") or 0.0)
            precision = float(metrics.get("precision_at_high_priority_proxy") or 0.0)
            pr_auc = float(metrics.get("pr_auc_proxy") or 0.0)
            control = float(metrics.get("control_high_priority_flag_rate_proxy") or 0.0)
            composite = (
                0.35 * rediscovery
                + 0.30 * precision
                + 0.25 * pr_auc
                + 0.10 * (1.0 - control)
            )
        ranked.append(
            {
                "model_key": entry["model_key"],
                "status": entry["status"],
                "composite_validation_score": composite,
                "metrics": metrics,
                "availability_reason": entry.get("availability_reason"),
            }
        )
    ranked.sort(
        key=lambda row: (
            row["composite_validation_score"] is not None,
            row["composite_validation_score"] or -1.0,
        ),
        reverse=True,
    )
    return ranked


async def _remote_case(
    model_key: str,
    case: ValidationCase,
    start: datetime,
    end: datetime,
    max_cloud: float,
) -> dict[str, Any]:
    bbox = bbox_from_center(case.lat, case.lon, case.radius_m)
    response = await infer_remote(
        model_key,
        {
            "task": "archaeological_anomaly_heatmap",
            "bbox": bbox,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "max_cloud": max_cloud,
            "output_contract": {
                "real_inference": True,
                "heatmap": "GeoJSON FeatureCollection<Point> with anomaly_score in [0,1]",
                "provenance": "required",
            },
        },
    )
    heatmap = response.get("heatmap")
    if not isinstance(heatmap, dict):
        raise RuntimeError(f"{model_key} endpoint did not return a heatmap.")
    evaluation = evaluate_heatmap(
        heatmap,
        center_lat=case.lat,
        center_lon=case.lon,
        radius_m=case.radius_m,
    )
    return {
        "evaluation": evaluation,
        "model_metadata": response.get("model_metadata", {}),
        "provenance": response.get("provenance", {}),
    }


@router.get("/foundation/models")
async def list_foundation_models() -> dict:
    registry = get_model_registry()
    return {
        "models": registry,
        "note": (
            "available=true only means a real inference endpoint is configured. "
            "HorusScan rejects responses that do not certify real_inference=true."
        ),
    }


@router.post("/tournament/run")
async def run_model_tournament(
    request: TournamentRequest,
    authorization: Annotated[str | None, Header()] = None,
) -> dict:
    if request.split not in {"calibration", "validation"}:
        raise HTTPException(
            status_code=422,
            detail="Model tournament is restricted to calibration or validation. Blind test is locked.",
        )

    token = _bearer(authorization)
    try:
        persistence = SupabasePersistence()
        user_id = await persistence.authenticate(token)
        sites = await persistence.list_validation_sites(request.dataset_version, request.split)
        all_centers = await persistence.list_all_validation_centers(request.dataset_version)
    except (PersistenceUnavailable, httpx.HTTPError, PermissionError) as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    if not sites:
        raise HTTPException(status_code=404, detail="No validation sites found.")

    requested = request.competitors or sorted(DETECTION_COMPETITORS)
    invalid = [key for key in requested if key not in DETECTION_COMPETITORS]
    if invalid:
        raise HTTPException(status_code=422, detail=f"Unsupported competitors: {invalid}")

    competitors = (["baseline_multisensor"] if request.include_baseline else []) + requested
    tournament_id = await persistence.create_tournament_run(
        user_id=user_id,
        dataset_version=request.dataset_version,
        split=request.split,
        competitors=competitors,
        parameters={
            "start": request.start.isoformat(),
            "end": request.end.isoformat(),
            "max_cloud": request.max_cloud,
            "include_unlabeled_controls": request.include_unlabeled_controls,
            "ranking_formula": (
                "0.35*rediscovery + 0.30*precision_high_priority + "
                "0.25*pr_auc_proxy + 0.10*(1-control_flag_rate)"
            ),
            "ranking_is_not_probability": True,
        },
        git_commit=__import__("os").getenv("GIT_COMMIT_SHA"),
    )

    forbidden = [
        (float(row["latitude"]), float(row["longitude"]), int(row["evaluation_radius_m"]))
        for row in all_centers
    ]

    cases: list[ValidationCase] = []
    for index, site in enumerate(sites):
        positive = ValidationCase(
            name=str(site["name"]),
            case_type="positive",
            lat=float(site["latitude"]),
            lon=float(site["longitude"]),
            radius_m=int(site["evaluation_radius_m"]),
            site_id=str(site["id"]),
        )
        cases.append(positive)
        if request.include_unlabeled_controls:
            cases.append(
                deterministic_control(
                    positive.lat,
                    positive.lon,
                    positive.radius_m,
                    index,
                    forbidden_centers=forbidden,
                )
            )

    registry_by_key = {row["key"]: row for row in get_model_registry()}
    entries: list[dict[str, Any]] = []

    for model_key in competitors:
        started = time.perf_counter()
        rows: list[dict[str, Any]] = []
        model_metadata: dict[str, Any] = {}
        availability_reason = None

        if model_key == "baseline_multisensor":
            status = "running"
        else:
            availability = registry_by_key[model_key]
            if not availability["available"]:
                status = "unavailable"
                availability_reason = availability["reason"]
                duration_ms = int((time.perf_counter() - started) * 1000)
                entry = {
                    "model_key": model_key,
                    "status": status,
                    "metrics": {},
                    "model_metadata": availability,
                    "availability_reason": availability_reason,
                    "duration_ms": duration_ms,
                }
                entries.append(entry)
                await persistence.store_tournament_entry(
                    tournament_id,
                    model_key,
                    status,
                    {},
                    availability,
                    availability_reason,
                    duration_ms,
                )
                continue
            status = "running"

        try:
            for case in cases:
                if model_key == "baseline_multisensor":
                    result = await run_validation_case(
                        case,
                        request.start,
                        request.end,
                        request.max_cloud,
                    )
                    evaluation = result["evaluation"]
                    model_metadata = {
                        "engine": "local_multisensor_baseline",
                        "sensors": ["sentinel2", "sentinel1_rtc", "copernicus_dem"],
                    }
                else:
                    result = await _remote_case(
                        model_key,
                        case,
                        request.start,
                        request.end,
                        request.max_cloud,
                    )
                    evaluation = result["evaluation"]
                    model_metadata = result.get("model_metadata", model_metadata)

                rows.append(
                    {
                        "case_type": case.case_type,
                        "case_name": case.name,
                        "rediscovery_score": evaluation.rediscovery_score,
                        "center_block_percentile": evaluation.center_block_percentile,
                        "concentration_ratio": evaluation.concentration_ratio,
                        "detected_high_priority": evaluation.detected_high_priority,
                    }
                )

            metrics = compute_benchmark_metrics(rows)
            status = "completed"
        except ModelUnavailable as exc:
            metrics = {}
            status = "unavailable"
            availability_reason = str(exc)
        except Exception as exc:
            metrics = {}
            status = "failed"
            availability_reason = f"{type(exc).__name__}: {exc}"

        duration_ms = int((time.perf_counter() - started) * 1000)
        entry = {
            "model_key": model_key,
            "status": status,
            "metrics": metrics,
            "model_metadata": model_metadata,
            "availability_reason": availability_reason,
            "duration_ms": duration_ms,
        }
        entries.append(entry)
        await persistence.store_tournament_entry(
            tournament_id,
            model_key,
            status,
            metrics,
            model_metadata,
            availability_reason,
            duration_ms,
        )

    ranking = _rank_entries(entries)
    completed = [row for row in ranking if row["status"] == "completed"]
    winner = completed[0]["model_key"] if completed else None
    run_status = "completed" if completed else "inconclusive"

    await persistence.finish_tournament_run(
        tournament_id,
        run_status,
        winner,
        ranking,
    )

    return {
        "tournament_run_id": tournament_id,
        "status": run_status,
        "winner": winner,
        "ranking": ranking,
        "entries": entries,
        "scientific_note": (
            "Tournament composite score is a validation ranking heuristic, not archaeological "
            "probability. Unlabeled controls make PR/ROC values proxy metrics."
        ),
    }
