from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx
import planetary_computer
from pystac_client import Client

from app.providers.stac import Scene, PLANETARY_COMPUTER_STAC


ENMAP_STAC = "https://geoservice.dlr.de/eoc/ogc/stac/v1"
NASA_CMR = "https://cmr.earthdata.nasa.gov/search"


async def _resolve_enmap_l2a_collection() -> str:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(f"{ENMAP_STAC}/collections")
        response.raise_for_status()
        collections = response.json().get("collections", [])
    for collection in collections:
        text = " ".join(
            str(collection.get(key, "")) for key in ("id", "title", "description")
        ).lower()
        if "enmap" in text and ("l2a" in text or "level 2a" in text):
            return str(collection["id"])
    raise RuntimeError("EnMAP L2A collection was not found in the DLR STAC catalogue.")


async def search_enmap_l2a(
    bbox: tuple[float, float, float, float],
    start: datetime,
    end: datetime,
    limit: int = 6,
) -> list[Scene]:
    collection_id = await _resolve_enmap_l2a_collection()
    payload = {
        "collections": [collection_id],
        "bbox": list(bbox),
        "datetime": f"{start.isoformat()}/{end.isoformat()}",
        "limit": limit,
    }
    async with httpx.AsyncClient(timeout=45.0) as client:
        response = await client.post(f"{ENMAP_STAC}/search", json=payload)
        response.raise_for_status()
        items = response.json().get("features", [])

    return [
        Scene(
            provider="dlr-eoc-geoservice",
            collection=item.get("collection", collection_id),
            item_id=item["id"],
            datetime=item.get("properties", {}).get("datetime")
            or item.get("properties", {}).get("start_datetime"),
            cloud_cover=item.get("properties", {}).get("eo:cloud_cover"),
            geometry=item.get("geometry"),
            assets=item.get("assets", {}),
            properties=item.get("properties", {}),
        )
        for item in items
    ]


async def search_emit_l2a(
    bbox: tuple[float, float, float, float],
    start: datetime,
    end: datetime,
    limit: int = 6,
) -> list[dict[str, Any]]:
    # CMR search is public; actual LP DAAC download may require Earthdata Login.
    params = {
        "short_name": "EMITL2ARFL",
        "bounding_box": ",".join(str(v) for v in bbox),
        "temporal": f"{start.isoformat()},{end.isoformat()}",
        "page_size": str(limit),
    }
    headers = {"accept": "application/json"}
    async with httpx.AsyncClient(timeout=45.0) as client:
        response = await client.get(f"{NASA_CMR}/granules.json", params=params, headers=headers)
        response.raise_for_status()
        entries = response.json().get("feed", {}).get("entry", [])

    results: list[dict[str, Any]] = []
    for entry in entries:
        links = [
            link.get("href")
            for link in entry.get("links", [])
            if link.get("href") and not link.get("inherited", False)
        ]
        results.append(
            {
                "provider": "nasa-earthdata-cmr",
                "collection": "EMITL2ARFL",
                "item_id": entry.get("id"),
                "title": entry.get("title"),
                "time_start": entry.get("time_start"),
                "time_end": entry.get("time_end"),
                "links": links,
                "download_requires_earthdata_auth": True,
            }
        )
    return results


def search_landsat_thermal(
    bbox: tuple[float, float, float, float],
    start: datetime,
    end: datetime,
    limit: int = 8,
) -> list[Scene]:
    catalog = Client.open(
        PLANETARY_COMPUTER_STAC,
        modifier=planetary_computer.sign_inplace,
    )
    search = catalog.search(
        collections=["landsat-c2-l2"],
        bbox=list(bbox),
        datetime=f"{start.isoformat()}/{end.isoformat()}",
        max_items=limit,
    )
    scenes: list[Scene] = []
    for item in search.items():
        signed = planetary_computer.sign(item)
        scenes.append(
            Scene(
                provider="microsoft-planetary-computer",
                collection=signed.collection_id or "landsat-c2-l2",
                item_id=signed.id,
                datetime=signed.datetime.isoformat() if signed.datetime else None,
                cloud_cover=signed.properties.get("eo:cloud_cover"),
                geometry=signed.geometry,
                assets={key: asset.to_dict() for key, asset in signed.assets.items()},
                properties=dict(signed.properties),
            )
        )
    return scenes
