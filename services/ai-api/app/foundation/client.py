from __future__ import annotations

import os
from typing import Any

import httpx

from app.foundation.registry import MODEL_SPECS


class ModelUnavailable(RuntimeError):
    pass


def _spec(model_key: str):
    for spec in MODEL_SPECS:
        if spec.key == model_key:
            return spec
    raise KeyError(f"Unknown model key: {model_key}")


async def infer_remote(
    model_key: str,
    payload: dict[str, Any],
    timeout_seconds: float = 180.0,
) -> dict[str, Any]:
    spec = _spec(model_key)
    endpoint = os.getenv(spec.endpoint_env)
    if not endpoint:
        raise ModelUnavailable(f"{model_key} unavailable: {spec.endpoint_env} is not configured.")

    headers = {"content-type": "application/json"}
    token_env = f"{model_key.upper()}_TOKEN"
    token = os.getenv(token_env)
    if token:
        headers["authorization"] = f"Bearer {token}"

    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        response = await client.post(endpoint.rstrip("/") + "/infer", json=payload, headers=headers)
        response.raise_for_status()
        result = response.json()

    if not result.get("real_inference", False):
        raise RuntimeError(
            f"{model_key} endpoint did not certify real_inference=true; refusing synthetic result."
        )
    return result
