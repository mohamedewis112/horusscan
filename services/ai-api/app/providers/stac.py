from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx


COPERNICUS_STAC = "https://stac.dataspace.copernicus.eu/v1"
EARTH_SEARCH_STAC = "https://earth-search.aws.element84.com/v1"


@dataclass(frozen=True)
class Scene:
    provider: str
    collection: str
    item_id: str
    datetime: str
    cloud_cover: float | None
    geometry: dict[str, Any]
    assets: dict[str, Any]


async def search_sentinel2(
    bbox: tuple[float, float, float, float],
    start: datetime,
    end: datetime,
    max_cloud: float = 20.0,
    limit: int = 10,
) -> list[Scene]:
    payload = {
        "collections": ["sentinel-2-l2a"],
        "bbox": list(bbox),
        "datetime": f"{start.isoformat()}/{end.isoformat()}",
        "limit": limit,
        "query": {"eo:cloud_cover": {"lte": max_cloud}},
        "sortby": [{"field": "properties.datetime", "direction": "desc"}],
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(f"{COPERNICUS_STAC}/search", json=payload)
        response.raise_for_status()
        items = response.json().get("features", [])

    return [
        Scene(
            provider="copernicus-data-space",
            collection=item["collection"],
            item_id=item["id"],
            datetime=item["properties"]["datetime"],
            cloud_cover=item["properties"].get("eo:cloud_cover"),
            geometry=item["geometry"],
            assets=item.get("assets", {}),
        )
        for item in items
    ]


async def search_open_cog_sentinel2(
    bbox: tuple[float, float, float, float],
    start: datetime,
    end: datetime,
    max_cloud: float = 20.0,
    limit: int = 5,
) -> list[Scene]:
    """Open-data fallback whose COG assets can be read without vendor credentials."""
    payload = {
        "collections": ["sentinel-2-l2a"],
        "bbox": list(bbox),
        "datetime": f"{start.isoformat()}/{end.isoformat()}",
        "limit": limit,
        "query": {"eo:cloud_cover": {"lte": max_cloud}},
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(f"{EARTH_SEARCH_STAC}/search", json=payload)
        response.raise_for_status()
        items = response.json().get("features", [])

    return [
        Scene(
            provider="earth-search",
            collection=item["collection"],
            item_id=item["id"],
            datetime=item["properties"]["datetime"],
            cloud_cover=item["properties"].get("eo:cloud_cover"),
            geometry=item["geometry"],
            assets=item.get("assets", {}),
        )
        for item in items
    ]
