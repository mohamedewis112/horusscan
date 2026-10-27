from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from worker.dinov3 import DinoV3SatelliteAdapter
from worker.optional import OptionalExternalAdapter


class Adapter(ABC):
    key: str

    @abstractmethod
    def available(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def unavailable_reason(self) -> str:
        raise NotImplementedError

    @abstractmethod
    async def infer(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError


_ADAPTERS = {
    "dinov3_satellite": DinoV3SatelliteAdapter(),
    "terramind": OptionalExternalAdapter("terramind"),
    "prithvi_eo_2": OptionalExternalAdapter("prithvi_eo_2"),
    "spectralearth_fm": OptionalExternalAdapter("spectralearth_fm"),
    "terraflow": OptionalExternalAdapter("terraflow"),
    "olmoearth": OptionalExternalAdapter("olmoearth"),
    "thermal_inertia": OptionalExternalAdapter("thermal_inertia"),
    "physics_sar": OptionalExternalAdapter("physics_sar"),
    "sam3_1": OptionalExternalAdapter("sam3_1"),
}


def get_adapter(key: str):
    return _ADAPTERS.get(key)
