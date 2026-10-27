from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from worker.adapters import get_adapter


app = FastAPI(
    title="HorusScan Foundation Worker",
    version="0.1.0",
    description="Runs real foundation-model inference outside the core API process.",
)


class InferenceRequest(BaseModel):
    task: str
    bbox: tuple[float, float, float, float]
    start: str
    end: str
    max_cloud: float = 20.0
    output_contract: dict | None = None


@app.get("/health")
async def health() -> dict:
    model_key = os.getenv("HORUS_MODEL_KEY", "")
    adapter = get_adapter(model_key) if model_key else None
    return {
        "status": "ok",
        "model_key": model_key or None,
        "adapter_available": bool(adapter and adapter.available()),
    }


@app.post("/infer")
async def infer(request: InferenceRequest) -> dict:
    model_key = os.getenv("HORUS_MODEL_KEY", "")
    if not model_key:
        raise HTTPException(status_code=503, detail="HORUS_MODEL_KEY is not configured.")

    adapter = get_adapter(model_key)
    if not adapter:
        raise HTTPException(status_code=404, detail=f"Unknown model adapter: {model_key}")
    if not adapter.available():
        raise HTTPException(
            status_code=503,
            detail=adapter.unavailable_reason(),
        )

    try:
        result = await adapter.infer(request.model_dump())
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"{model_key} real inference failed: {type(exc).__name__}: {exc}",
        ) from exc

    if not result.get("real_inference"):
        raise HTTPException(
            status_code=500,
            detail="Adapter result rejected: real_inference=true is mandatory.",
        )
    return result


@app.get("/smoke")
async def smoke() -> dict:
    model_key = os.getenv("HORUS_MODEL_KEY", "dinov3_satellite")
    adapter = get_adapter(model_key)
    if not adapter or not adapter.available():
        raise HTTPException(status_code=503, detail=adapter.unavailable_reason() if adapter else "Adapter unavailable.")
    result = await adapter.infer({
        "task": "spatial_anomaly_heatmap",
        "bbox": (31.20, 29.92, 31.22, 29.94),
        "start": "2026-04-01T00:00:00Z",
        "end": "2026-10-26T23:59:59Z",
        "max_cloud": 25.0,
    })
    features = result.get("heatmap", {}).get("features", [])
    scores = [
        float(item.get("properties", {}).get("anomaly_score"))
        for item in features
        if isinstance(item.get("properties", {}).get("anomaly_score"), (int, float))
    ]
    return {
        "status": "completed",
        "real_inference": result.get("real_inference") is True,
        "model_metadata": result.get("model_metadata", {}),
        "feature_count": len(features),
        "score_min": min(scores) if scores else None,
        "score_max": max(scores) if scores else None,
        "provenance": result.get("provenance", {}),
    }
