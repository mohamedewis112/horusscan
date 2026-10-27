from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx
import planetary_computer
from pystac_client import Client


COPERNICUS_STAC = "https://stac.dataspace.copernicus.eu/v1"
EARTH_SEARCH_STAC = "https://earth-search.aws.element84.com/v1"
PLANETARY_COMPUTER_STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"


@dataclass(frozen=True)
class Scene:
    provider: str
    collection: str
    item_id: str
    datetime: str
    cloud_cover: float | None
    geometry: dict[str, Any]
    assets: dict[str, Any]
    properties: dict[str, Any]


def _scene_from_stac_item(item: Any, provider: str) -> Scene:
    item = planetary_computer.sign(item)
    return Scene(
        provider=provider,
        collection=item.collection_id or "unknown",
        item_id=item.id,
        datetime=item.datetime.isoformat() if item.datetime else str(item.properties.get("datetime")),
        cloud_cover=item.properties.get("eo:cloud_cover"),
        geometry=item.geometry,
        assets={key: asset.to_dict() for key, asset in item.assets.items()},
        properties=dict(item.properties),
    )


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
            properties=item.get("properties", {}),
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
            properties=item.get("properties", {}),
        )
        for item in items
    ]


def search_sentinel1_rtc(
    bbox: tuple[float, float, float, float],
    start: datetime,
    end: datetime,
    limit: int = 6,
) -> list[Scene]:
    catalog = Client.open(
        PLANETARY_COMPUTER_STAC,
        modifier=planetary_computer.sign_inplace,
    )
    search = catalog.search(
        collections=["sentinel-1-rtc"],
        bbox=list(bbox),
        datetime=f"{start.isoformat()}/{end.isoformat()}",
        max_items=limit,
    )
    items = list(search.items())
    items.sort(key=lambda item: item.datetime or datetime.min, reverse=True)
    return [_scene_from_stac_item(item, "microsoft-planetary-computer") for item in items[:limit]]


def search_copernicus_dem(
    bbox: tuple[float, float, float, float],
    limit: int = 8,
) -> list[Scene]:
    catalog = Client.open(
        PLANETARY_COMPUTER_STAC,
        modifier=planetary_computer.sign_inplace,
    )
    search = catalog.search(
        collections=["cop-dem-glo-30"],
        bbox=list(bbox),
        max_items=limit,
    )
    items = list(search.items())
    return [_scene_from_stac_item(item, "microsoft-planetary-computer") for item in items[:limit]]
