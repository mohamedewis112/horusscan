from __future__ import annotations

import asyncio
import os
from datetime import datetime
from math import isqrt
from typing import Any

import numpy as np
import rasterio
import torch
from PIL import Image
from pystac_client import Client
from rasterio.enums import Resampling
from rasterio.warp import transform_bounds
from rasterio.windows import from_bounds
from transformers import AutoImageProcessor, AutoModel


EARTH_SEARCH = "https://earth-search.aws.element84.com/v1"


class DinoV3SatelliteAdapter:
    key = "dinov3_satellite"

    def __init__(self) -> None:
        self.model_id = os.getenv(
            "HORUS_DINOV3_MODEL_ID",
            "facebook/dinov3-vitl16-pretrain-sat493m",
        )
        self._processor = None
        self._model = None

    def available(self) -> bool:
        return torch.cuda.is_available() or os.getenv("HORUS_ALLOW_CPU_FOUNDATION", "0") == "1"

    def unavailable_reason(self) -> str:
        return (
            "DINOv3 adapter requires CUDA by default. "
            "Set HORUS_ALLOW_CPU_FOUNDATION=1 only for deliberate slow CPU inference."
        )

    def _load(self):
        if self._processor is None:
            self._processor = AutoImageProcessor.from_pretrained(self.model_id)
        if self._model is None:
            self._model = AutoModel.from_pretrained(self.model_id)
            device = "cuda" if torch.cuda.is_available() else "cpu"
            self._model = self._model.to(device).eval()
        return self._processor, self._model

    @staticmethod
    def _read_band(href: str, bbox, shape=(512, 512)) -> np.ndarray:
        with rasterio.Env(GDAL_HTTP_MULTIRANGE="YES", GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR"):
            with rasterio.open(href) as src:
                bounds = transform_bounds("EPSG:4326", src.crs, *bbox, densify_pts=21)
                window = from_bounds(*bounds, transform=src.transform)
                data = src.read(
                    1,
                    window=window,
                    out_shape=shape,
                    resampling=Resampling.bilinear,
                    masked=True,
                )
        return data.filled(np.nan).astype(np.float32)

    @staticmethod
    def _asset(assets: dict[str, Any], names: tuple[str, ...]) -> str:
        for name in names:
            asset = assets.get(name)
            if asset and asset.href:
                return asset.href
        raise KeyError(f"Missing Sentinel-2 asset: {names}")

    def _prepare_rgb(self, bbox, start: str, end: str, max_cloud: float):
        catalog = Client.open(EARTH_SEARCH)
        search = catalog.search(
            collections=["sentinel-2-l2a"],
            bbox=list(bbox),
            datetime=f"{start}/{end}",
            query={"eo:cloud_cover": {"lte": max_cloud}},
            max_items=5,
        )
        items = list(search.items())
        if not items:
            raise ValueError("No Sentinel-2 scene available for DINOv3.")

        items.sort(key=lambda item: item.properties.get("eo:cloud_cover", 101.0))
        item = items[0]
        red = self._read_band(self._asset(item.assets, ("red", "B04")), bbox)
        green = self._read_band(self._asset(item.assets, ("green", "B03")), bbox)
        blue = self._read_band(self._asset(item.assets, ("blue", "B02")), bbox)
        rgb = np.stack([red, green, blue], axis=-1)

        valid = np.all(np.isfinite(rgb), axis=-1)
        if valid.sum() < 512:
            raise ValueError("Insufficient valid RGB pixels for DINOv3.")

        output = np.zeros_like(rgb, dtype=np.uint8)
        for band in range(3):
            values = rgb[..., band][valid]
            low, high = np.quantile(values, [0.02, 0.98])
            scaled = np.clip((rgb[..., band] - low) / max(high - low, 1e-6), 0.0, 1.0)
            output[..., band] = np.nan_to_num(scaled * 255.0, nan=0.0).astype(np.uint8)

        return Image.fromarray(output, mode="RGB"), item

    @staticmethod
    def _heatmap_from_embeddings(tokens: torch.Tensor, bbox) -> dict[str, Any]:
        token_count = tokens.shape[0]
        side = isqrt(token_count)
        if side * side != token_count:
            side = isqrt(token_count - 1)
            if side * side <= token_count:
                tokens = tokens[-(side * side):]
            else:
                raise ValueError("Could not infer DINOv3 patch-token grid.")
        else:
            tokens = tokens[: side * side]

        normalized = torch.nn.functional.normalize(tokens.float(), dim=-1)
        center = torch.nn.functional.normalize(normalized.mean(dim=0, keepdim=True), dim=-1)
        distance = 1.0 - (normalized @ center.T).squeeze(-1)
        values = distance.detach().cpu().numpy()
        low, high = np.quantile(values, [0.01, 0.99])
        score = np.clip((values - low) / max(high - low, 1e-6), 0.0, 1.0).reshape(side, side)

        min_lon, min_lat, max_lon, max_lat = bbox
        features = []
        for row in range(side):
            for col in range(side):
                lon = min_lon + ((col + 0.5) / side) * (max_lon - min_lon)
                lat = max_lat - ((row + 0.5) / side) * (max_lat - min_lat)
                features.append(
                    {
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [lon, lat]},
                        "properties": {"anomaly_score": float(score[row, col])},
                    }
                )
        return {"type": "FeatureCollection", "features": features}

    def _infer_sync(self, payload: dict[str, Any]) -> dict[str, Any]:
        processor, model = self._load()
        image, item = self._prepare_rgb(
            payload["bbox"],
            payload["start"],
            payload["end"],
            float(payload.get("max_cloud", 20.0)),
        )

        inputs = processor(images=image, return_tensors="pt")
        device = next(model.parameters()).device
        inputs = {key: value.to(device) for key, value in inputs.items()}

        with torch.inference_mode():
            output = model(**inputs)

        hidden = getattr(output, "last_hidden_state", None)
        if hidden is None:
            raise RuntimeError("DINOv3 output did not contain last_hidden_state.")

        heatmap = self._heatmap_from_embeddings(hidden[0], payload["bbox"])
        return {
            "real_inference": True,
            "heatmap": heatmap,
            "model_metadata": {
                "model_key": self.key,
                "model_id": self.model_id,
                "device": str(device),
                "token_count": int(hidden.shape[1]),
                "embedding_dim": int(hidden.shape[2]),
            },
            "provenance": {
                "provider": "earth-search",
                "collection": item.collection_id,
                "item_id": item.id,
                "datetime": item.datetime.isoformat() if item.datetime else None,
                "cloud_cover": item.properties.get("eo:cloud_cover"),
                "method": "DINOv3 dense embedding distance-to-context",
                "not_probability": True,
            },
        }

    async def infer(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await asyncio.to_thread(self._infer_sync, payload)
