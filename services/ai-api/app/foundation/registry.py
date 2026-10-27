from __future__ import annotations

import os
from dataclasses import dataclass, asdict
from typing import Any


@dataclass(frozen=True)
class ModelSpec:
    key: str
    family: str
    role: str
    endpoint_env: str
    checkpoint_env: str | None
    modalities: tuple[str, ...]
    heavy: bool = True

    def availability(self) -> dict[str, Any]:
        endpoint = os.getenv(self.endpoint_env)
        checkpoint = os.getenv(self.checkpoint_env) if self.checkpoint_env else None
        available = bool(endpoint)
        return {
            **asdict(self),
            "available": available,
            "endpoint_configured": bool(endpoint),
            "checkpoint_configured": bool(checkpoint) if self.checkpoint_env else None,
            "reason": None if available else f"Set {self.endpoint_env} to a real inference endpoint.",
        }


MODEL_SPECS = (
    ModelSpec(
        key="terramind",
        family="earth-foundation-model",
        role="multimodal_encoder",
        endpoint_env="TERRAMIND_INFERENCE_URL",
        checkpoint_env="TERRAMIND_CHECKPOINT",
        modalities=("sentinel2", "sentinel1_rtc", "dem", "ndvi", "coordinates"),
    ),
    ModelSpec(
        key="prithvi_eo_2",
        family="earth-foundation-model",
        role="spatiotemporal_encoder",
        endpoint_env="PRITHVI_INFERENCE_URL",
        checkpoint_env="PRITHVI_CHECKPOINT",
        modalities=("sentinel2_time_series", "timestamps", "coordinates"),
    ),
    ModelSpec(
        key="dinov3_satellite",
        family="vision-foundation-model",
        role="dense_geometry_encoder",
        endpoint_env="DINOV3_SAT_INFERENCE_URL",
        checkpoint_env="DINOV3_SAT_CHECKPOINT",
        modalities=("satellite_rgb_nir",),
    ),
    ModelSpec(
        key="sam3_1",
        family="segmentation-foundation-model",
        role="geometry_segmenter",
        endpoint_env="SAM31_INFERENCE_URL",
        checkpoint_env="SAM31_CHECKPOINT",
        modalities=("image", "point_prompts", "box_prompts", "text_prompts"),
    ),
    ModelSpec(
        key="spectralearth_fm",
        family="hyperspectral-foundation-model",
        role="spectral_multimodal_encoder",
        endpoint_env="SPECTRALEARTH_INFERENCE_URL",
        checkpoint_env="SPECTRALEARTH_CHECKPOINT",
        modalities=("hyperspectral", "sentinel2", "landsat", "lst", "sentinel1"),
    ),
    ModelSpec(
        key="terraflow",
        family="earth-foundation-model",
        role="multimodal_temporal_encoder",
        endpoint_env="TERRAFLOW_INFERENCE_URL",
        checkpoint_env="TERRAFLOW_CHECKPOINT",
        modalities=("multimodal_time_series",),
    ),
    ModelSpec(
        key="olmoearth",
        family="earth-foundation-model",
        role="global_spatiotemporal_embedding",
        endpoint_env="OLMOEARTH_INFERENCE_URL",
        checkpoint_env=None,
        modalities=("global_10m_embeddings", "annual_embeddings", "multimodal"),
    ),
    ModelSpec(
        key="thermal_inertia",
        family="physics-informed-model",
        role="day_night_thermal_inertia_estimation",
        endpoint_env="THERMAL_INERTIA_INFERENCE_URL",
        checkpoint_env="THERMAL_INERTIA_CHECKPOINT",
        modalities=("ecostress_day_night", "landsat_lst", "surface_context"),
    ),
    ModelSpec(
        key="physics_sar",
        family="physics-informed-model",
        role="soil_moisture_roughness_inversion",
        endpoint_env="PHYSICS_SAR_INFERENCE_URL",
        checkpoint_env="PHYSICS_SAR_CHECKPOINT",
        modalities=("sentinel1_rtc", "optical_context"),
    ),
)


def get_model_registry() -> list[dict[str, Any]]:
    return [spec.availability() for spec in MODEL_SPECS]
