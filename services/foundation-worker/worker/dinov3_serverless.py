from __future__ import annotations

import asyncio
import os
from io import BytesIO
from math import isqrt
from typing import Any

import httpx
import numpy as np
import torch
from PIL import Image
from transformers import AutoImageProcessor, AutoModel


STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
TILER = "https://planetarycomputer.microsoft.com/api/data/v1"


class DinoV3ServerlessAdapter:
    key = "dinov3_satellite"

    def __init__(self) -> None:
        self.model_id = os.getenv(
            "HORUS_DINOV3_MODEL_ID",
            "facebook/dinov3-vitl16-pretrain-sat493m",
        )
        self.processor = None
        self.model = None

    def available(self) -> bool:
        return os.getenv("HORUS_ALLOW_CPU_FOUNDATION", "0") == "1" or torch.cuda.is_available()

    def unavailable_reason(self) -> str:
        return "DINOv3 requires an enabled CPU smoke runtime or CUDA runtime."

    def _load(self):
        if self.processor is None:
            self.processor = AutoImageProcessor.from_pretrained(self.model_id)
        if self.model is None:
            self.model = AutoModel.from_pretrained(self.model_id).to("cpu").eval()
        return self.processor, self.model

    async def _scene(self, payload: dict[str, Any]) -> dict[str, Any]:
        search = {
            "collections": ["sentinel-2-l2a"],
            "bbox": list(payload["bbox"]),
            "datetime": f'{payload["start"]}/{payload["end"]}',
            "limit": 5,
            "query": {"eo:cloud_cover": {"lte": float(payload.get("max_cloud", 20.0))}},
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(f"{STAC}/search", json=search)
            response.raise_for_status()
            items = response.json().get("features", [])
        if not items:
            raise RuntimeError("No Sentinel-2 scene available.")
        return min(items, key=lambda x: x.get("properties", {}).get("eo:cloud_cover", 101.0))

    async def _image(self, item: dict[str, Any], bbox) -> Image.Image:
        w, s, e, n = bbox
        url = f"{TILER}/item/bbox/{w},{s},{e},{n}/256x256.npy"
        params = [
            ("collection", "sentinel-2-l2a"),
            ("item", item["id"]),
            ("asset_as_band", "true"),
            ("return_mask", "true"),
            ("resampling", "bilinear"),
            ("unscale", "false"),
            ("assets", "B04"),
            ("assets", "B03"),
            ("assets", "B02"),
        ]
        async with httpx.AsyncClient(timeout=90.0) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
        array = np.load(BytesIO(response.content), allow_pickle=False)
        rgb = np.moveaxis(array[:3].astype(np.float32), 0, -1)
        mask = array[-1] > 0 if array.shape[0] > 3 else np.all(np.isfinite(rgb), axis=-1)
        valid = mask & np.all(np.isfinite(rgb), axis=-1)
        if int(valid.sum()) < 512:
            raise RuntimeError("Insufficient valid RGB pixels.")
        out = np.zeros_like(rgb, dtype=np.uint8)
        for band in range(3):
            values = rgb[..., band][valid]
            lo, hi = np.quantile(values, [0.02, 0.98])
            scaled = np.clip((rgb[..., band] - lo) / max(float(hi - lo), 1e-6), 0, 1)
            out[..., band] = np.nan_to_num(scaled * 255.0, nan=0.0).astype(np.uint8)
        return Image.fromarray(out, mode="RGB")

    def _run(self, image: Image.Image, bbox) -> tuple[dict[str, Any], dict[str, Any]]:
        processor, model = self._load()
        inputs = processor(images=image, return_tensors="pt")
        with torch.inference_mode():
            output = model(**inputs)
        hidden = output.last_hidden_state[0]
        count = hidden.shape[0]
        side = isqrt(count)
        if side * side != count:
            side = isqrt(count - 1)
            hidden = hidden[-side * side:]
        vectors = torch.nn.functional.normalize(hidden.float(), dim=-1)
        center = torch.nn.functional.normalize(vectors.mean(dim=0, keepdim=True), dim=-1)
        values = (1 - (vectors @ center.T).squeeze(-1)).cpu().numpy()
        lo, hi = np.quantile(values, [0.01, 0.99])
        score = np.clip((values - lo) / max(float(hi - lo), 1e-6), 0, 1).reshape(side, side)

        w, s, e, n = bbox
        features = []
        for row in range(side):
            for col in range(side):
                features.append({
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [
                            w + ((col + 0.5) / side) * (e - w),
                            n - ((row + 0.5) / side) * (n - s),
                        ],
                    },
                    "properties": {"anomaly_score": float(score[row, col])},
                })
        return (
            {"type": "FeatureCollection", "features": features},
            {
                "model_key": self.key,
                "model_id": self.model_id,
                "device": "cpu",
                "embedding_dim": int(hidden.shape[-1]),
            },
        )

    async def infer(self, payload: dict[str, Any]) -> dict[str, Any]:
        item = await self._scene(payload)
        image = await self._image(item, payload["bbox"])
        heatmap, metadata = await asyncio.to_thread(self._run, image, payload["bbox"])
        return {
            "real_inference": True,
            "heatmap": heatmap,
            "model_metadata": metadata,
            "provenance": {
                "provider": "Microsoft Planetary Computer",
                "item_id": item["id"],
                "method": "DINOv3 SAT-493M embedding anomaly rank",
                "not_probability": True,
            },
        }
