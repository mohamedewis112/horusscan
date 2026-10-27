from __future__ import annotations

import os
from typing import Any

import httpx

from app.providers.stac import Scene


class PersistenceUnavailable(RuntimeError):
    pass


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
            "assets": scene.assets,
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
