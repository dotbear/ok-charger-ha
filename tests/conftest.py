"""Stub out Home Assistant + aiohttp imports so coordinator.py can be imported
without a real HA install. We only need shells of the symbols touched at module
load time (class definitions, type aliases, exception classes)."""

from __future__ import annotations

import sys
import types
from typing import Generic, TypeVar

_T = TypeVar("_T")


def _package(name: str) -> types.ModuleType:
    mod = types.ModuleType(name)
    mod.__path__ = []  # mark as a package so submodule imports work
    sys.modules[name] = mod
    return mod


def _module(name: str) -> types.ModuleType:
    mod = types.ModuleType(name)
    sys.modules[name] = mod
    return mod


_module("aiohttp")

_package("homeassistant")
ha_core = _module("homeassistant.core")
_package("homeassistant.helpers")
ha_entity = _module("homeassistant.helpers.entity")
ha_uc = _module("homeassistant.helpers.update_coordinator")


class _HomeAssistant:
    pass


class _DeviceInfo(dict):
    pass


class _DataUpdateCoordinator(Generic[_T]):
    def __init__(self, *args, **kwargs) -> None:
        pass


class _UpdateFailed(Exception):
    pass


ha_core.HomeAssistant = _HomeAssistant
ha_entity.DeviceInfo = _DeviceInfo
ha_uc.DataUpdateCoordinator = _DataUpdateCoordinator
ha_uc.UpdateFailed = _UpdateFailed
