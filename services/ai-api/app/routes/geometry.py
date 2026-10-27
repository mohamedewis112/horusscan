from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.foundation.client import ModelUnavailable, infer_remote


router = APIRouter(prefix="/v1/foundation", tags=["geometry"])


class GeometryRequest(BaseModel):
    bbox: tuple[float, float, float, float]
    heatmap: dict[str, Any]
    top_k: int = Field(default=8, ge=1, le=50)


def _top_candidates(heatmap: dict[str, Any], top_k: int) -> list[dict[str, Any]]:
    features = heatmap.get("features", [])
    candidates = [
        feature
        for feature in features
        if isinstance(feature, dict)
        and isinstance(feature.get("properties"), dict)
        and isinstance(feature["properties"].get("anomaly_score"), (int, float))
    ]
    candidates.sort(
        key=lambda feature: float(feature["properties"]["anomaly_score"]),
        reverse=True,
    )
    return candidates[:top_k]


@router.post("/segment-candidates")
async def segment_candidates(request: GeometryRequest) -> dict:
    candidates = _top_candidates(request.heatmap, request.top_k)
    if not candidates:
        raise HTTPException(status_code=422, detail="No candidate points in heatmap.")

    try:
        result = await infer_remote(
            "sam3_1",
            {
                "task": "segment_anomaly_candidates",
                "bbox": request.bbox,
                "candidate_points": [
                    {
                        "coordinates": feature["geometry"]["coordinates"],
                        "anomaly_score": feature["properties"]["anomaly_score"],
                    }
                    for feature in candidates
                ],
                "output_contract": {
                    "real_inference": True,
                    "segments": "GeoJSON FeatureCollection<Polygon|MultiPolygon>",
                    "provenance": "required",
                },
            },
        )
    except ModelUnavailable as exc:
        return {
            "status": "unavailable",
            "segments": None,
            "reason": str(exc),
            "fallback": "HyperFusion heatmap remains authoritative; no synthetic polygons created.",
        }
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"SAM 3.1 geometry post-processing failed: {type(exc).__name__}: {exc}",
        ) from exc

    segments = result.get("segments")
    if not isinstance(segments, dict):
        raise HTTPException(status_code=502, detail="SAM 3.1 endpoint returned no GeoJSON segments.")

    return {
        "status": "completed",
        "real_inference": True,
        "segments": segments,
        "model_metadata": result.get("model_metadata", {}),
        "provenance": result.get("provenance", {}),
        "scientific_note": (
            "SAM segmentation delineates image geometry around preselected anomalies. "
            "It does not independently establish archaeological origin or subsurface depth."
        ),
    }
