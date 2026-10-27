from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.providers.stac import Scene


class PersistenceUnavailable(RuntimeError):
    pass


def _sanitize_assets(assets: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key, value in assets.items():
        if not isinstance(value, dict):
            sanitized[key] = value
            continue
        clean = dict(value)
        href = clean.get("href")
        if isinstance(href, str):
            parts = urlsplit(href)
            clean["href"] = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
        sanitized[key] = clean
    return sanitized


class SupabasePersistence:
    def __init__(self) -> None:
        self.url = os.getenv("SUPABASE_URL", "").rstrip("/")
        self.publishable_key = os.getenv("SUPABASE_PUBLISHABLE_KEY", "")
        self.service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
        if not self.url or not self.publishable_key or not self.service_key:
            raise PersistenceUnavailable(
                "SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY and SUPABASE_SERVICE_ROLE_KEY "
                "must be configured on the AI server."
            )

    async def authenticate(self, bearer_token: str) -> str:
        headers = {
            "apikey": self.publishable_key,
            "authorization": f"Bearer {bearer_token}",
        }
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(f"{self.url}/auth/v1/user", headers=headers)
            response.raise_for_status()
            payload = response.json()
        user_id = payload.get("id")
        if not user_id:
            raise PermissionError("Authenticated Supabase user id was not returned.")
        return str(user_id)

    def _service_headers(self, prefer: str | None = None) -> dict[str, str]:
        headers = {
            "apikey": self.service_key,
            "authorization": f"Bearer {self.service_key}",
            "content-type": "application/json",
        }
        if prefer:
            headers["prefer"] = prefer
        return headers

    async def assert_run_ownership(
        self,
        user_id: str,
        project_id: str,
        aoi_id: str,
        analysis_run_id: str,
    ) -> None:
        params = {
            "id": f"eq.{analysis_run_id}",
            "project_id": f"eq.{project_id}",
            "aoi_id": f"eq.{aoi_id}",
            "requested_by": f"eq.{user_id}",
            "select": "id",
        }
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(
                f"{self.url}/rest/v1/analysis_runs",
                headers=self._service_headers(),
                params=params,
            )
            response.raise_for_status()
            rows = response.json()
        if not rows:
            raise PermissionError("Analysis run does not belong to the authenticated user.")

    async def update_run(self, analysis_run_id: str, values: dict[str, Any]) -> None:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.patch(
                f"{self.url}/rest/v1/analysis_runs",
                headers=self._service_headers("return=minimal"),
                params={"id": f"eq.{analysis_run_id}"},
                json=values,
            )
            response.raise_for_status()

    async def store_scene(self, scene: Scene, sensor: str) -> str:
        payload = {
            "provider": scene.provider,
            "collection": scene.collection,
            "item_id": scene.item_id,
            "sensor": sensor,
            "acquisition_time": scene.datetime,
            "cloud_cover": scene.cloud_cover,
            "assets": _sanitize_assets(scene.assets),
            "metadata": {
                "properties": scene.properties,
                "geometry": scene.geometry,
            },
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self.url}/rest/v1/satellite_scenes",
                headers=self._service_headers("resolution=merge-duplicates,return=representation"),
                params={"on_conflict": "provider,collection,item_id"},
                json=payload,
            )
            response.raise_for_status()
            rows = response.json()
        if not rows:
            raise RuntimeError("Supabase did not return the persisted satellite scene.")
        return str(rows[0]["id"])

    async def link_scene(
        self,
        analysis_run_id: str,
        scene_id: str,
        sensor_role: str,
        ordinal: int,
    ) -> None:
        payload = {
            "analysis_run_id": analysis_run_id,
            "satellite_scene_id": scene_id,
            "sensor_role": sensor_role,
            "ordinal": ordinal,
        }
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                f"{self.url}/rest/v1/analysis_run_scenes",
                headers=self._service_headers("resolution=merge-duplicates,return=minimal"),
                params={"on_conflict": "analysis_run_id,satellite_scene_id,sensor_role"},
                json=payload,
            )
            response.raise_for_status()

    async def store_layer(
        self,
        analysis_run_id: str,
        layer_type: str,
        stats: dict[str, Any],
        provenance: dict[str, Any],
        data: dict[str, Any],
    ) -> None:
        payload = {
            "analysis_run_id": analysis_run_id,
            "layer_type": layer_type,
            "stats": stats,
            "provenance": provenance,
            "data": data,
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self.url}/rest/v1/analysis_layers",
                headers=self._service_headers("return=minimal"),
                json=payload,
            )
            response.raise_for_status()


    async def list_validation_sites(
        self,
        dataset_version: str,
        split: str,
    ) -> list[dict[str, Any]]:
        params = {
            "dataset_version": f"eq.{dataset_version}",
            "select": (
                "id,name,region_group,split,evaluation_radius_m,"
                "center_geojson:st_asgeojson(center)"
            ),
        }
        if split != "all":
            params["split"] = f"eq.{split}"

        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(
                f"{self.url}/rest/v1/validation_sites",
                headers=self._service_headers(),
                params=params,
            )
            response.raise_for_status()
            return list(response.json())

    async def create_validation_run(
        self,
        user_id: str,
        dataset_version: str,
        split: str,
        parameters: dict[str, Any],
        git_commit: str | None,
    ) -> str:
        payload = {
            "requested_by": user_id,
            "dataset_version": dataset_version,
            "split": split,
            "status": "running",
            "parameters": parameters,
            "git_commit": git_commit,
            "started_at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
        }
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                f"{self.url}/rest/v1/validation_runs",
                headers=self._service_headers("return=representation"),
                json=payload,
            )
            response.raise_for_status()
            rows = response.json()
        if not rows:
            raise RuntimeError("Supabase did not return the validation run.")
        return str(rows[0]["id"])

    async def update_validation_run(
        self,
        validation_run_id: str,
        values: dict[str, Any],
    ) -> None:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.patch(
                f"{self.url}/rest/v1/validation_runs",
                headers=self._service_headers("return=minimal"),
                params={"id": f"eq.{validation_run_id}"},
                json=values,
            )
            response.raise_for_status()

    async def store_validation_result(
        self,
        validation_run_id: str,
        validation_site_id: str | None,
        case_type: str,
        case_name: str,
        lon: float,
        lat: float,
        radius_m: int,
        rediscovery_score: float | None,
        center_block_percentile: float | None,
        concentration_ratio: float | None,
        detected_high_priority: bool | None,
        fusion_summary: dict[str, Any],
        sensor_summary: dict[str, Any],
        error_message: str | None = None,
    ) -> None:
        payload = {
            "validation_run_id": validation_run_id,
            "validation_site_id": validation_site_id,
            "case_type": case_type,
            "case_name": case_name,
            "evaluated_center": f"POINT({lon} {lat})",
            "evaluation_radius_m": radius_m,
            "rediscovery_score": rediscovery_score,
            "center_block_percentile": center_block_percentile,
            "concentration_ratio": concentration_ratio,
            "detected_high_priority": detected_high_priority,
            "fusion_summary": fusion_summary,
            "sensor_summary": sensor_summary,
            "error_message": error_message,
        }
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                f"{self.url}/rest/v1/validation_results",
                headers=self._service_headers("return=minimal"),
                json=payload,
            )
            response.raise_for_status()
