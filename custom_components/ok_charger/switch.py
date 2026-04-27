"""Charge start/stop switch entity for OK Charger."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import OkChargerCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: OkChargerCoordinator = hass.data[DOMAIN][entry.entry_id]
    if coordinator.data.station is None:
        return
    async_add_entities([ChargeSwitch(coordinator, entry.entry_id)])


class ChargeSwitch(CoordinatorEntity[OkChargerCoordinator], SwitchEntity):
    """Toggle to start/stop a charging session."""

    _attr_has_entity_name = True
    _attr_name = "Charge"
    _attr_icon = "mdi:ev-station"

    def __init__(self, coordinator: OkChargerCoordinator, entry_id: str) -> None:
        super().__init__(coordinator)
        station = coordinator.data.station
        assert station is not None
        self._attr_unique_id = f"{station.cs_identifier}_charge_switch"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, station.cs_identifier)},
            manufacturer=station.vendor or "Peblar",
            model=station.model,
            sw_version=station.firmware_version,
            name=station.name,
            serial_number=station.serial_number,
        )

    @property
    def is_on(self) -> bool:
        station = self.coordinator.data.station
        return bool(station and station.is_charging)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_start_charge()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_stop_charge()
