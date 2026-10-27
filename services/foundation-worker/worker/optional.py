from __future__ import annotations

import importlib
import inspect
import os
from typing import Any


class OptionalExternalAdapter:
    def __init__(self, key: str) -> None:
        self.key = key
        self.env_name = f"HORUS_PLUGIN_MODULE_{key.upper()}"

    def _module_name(self) -> str | None:
        return os.getenv(self.env_name)

    def available(self) -> bool:
        module_name = self._module_name()
        if not module_name:
            return False
        try:
            module = importlib.import_module(module_name)
        except Exception:
            return False
        return callable(getattr(module, "infer", None))

    def unavailable_reason(self) -> str:
        return (
            f"{self.key} plugin is not installed/configured. "
            f"Set {self.env_name} to a Python module exposing infer(payload)."
        )

    async def infer(self, payload: dict[str, Any]) -> dict[str, Any]:
        module_name = self._module_name()
        if not module_name:
            raise RuntimeError(self.unavailable_reason())
        module = importlib.import_module(module_name)
        infer = getattr(module, "infer")
        result = infer(payload)
        if inspect.isawaitable(result):
            result = await result
        if not isinstance(result, dict):
            raise TypeError(f"{self.key} plugin must return a dict.")
        if result.get("real_inference") is not True:
            raise RuntimeError(
                f"{self.key} plugin result rejected because real_inference=true is missing."
            )
        return result
