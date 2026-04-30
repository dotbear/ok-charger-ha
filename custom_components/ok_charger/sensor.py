"""Sensor entities for the OK Charger integration."""

from __future__ import annotations

import datetime as dt

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import OkChargerCoordinator, device_info_for


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: OkChargerCoordinator = hass.data[DOMAIN][entry.entry_id]
    if coordinator.data.station is None:
        return
    async_add_entities(
        [
            CurrentPriceSensor(coordinator),
            CheapestWindowStartSensor(coordinator),
            CheapestWindowEndSensor(coordinator),
            CheapestWindowPriceSensor(coordinator),
            ChargingStateSensor(coordinator),
        ]
    )


class _OkSensorBase(CoordinatorEntity[OkChargerCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: OkChargerCoordinator) -> None:
        super().__init__(coordinator)
        station = coordinator.data.station
        assert station is not None
        self._station_id = station.cs_identifier
        self._attr_device_info = device_info_for(station)


class CurrentPriceSensor(_OkSensorBase):
    """All-in spot price for the current hour, in øre/kWh."""

    _attr_name = "Current price"
    _attr_native_unit_of_measurement = "øre/kWh"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:cash"

    def __init__(self, coordinator: OkChargerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._station_id}_current_price"

    @property
    def native_value(self) -> int | None:
        prices = self.coordinator.data.prices
        if not prices:
            return None
        now = dt.datetime.now(tz=dt.timezone.utc)
        for p in prices:
            if p.applicable_time <= now < p.applicable_time + dt.timedelta(hours=1):
                return p.total_ore_per_kwh
        return None


class CheapestWindowStartSensor(_OkSensorBase):
    """When today's/tomorrow's cheapest contiguous N-hour window starts."""

    _attr_name = "Cheapest window start"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:clock-start"

    def __init__(self, coordinator: OkChargerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._station_id}_cheapest_start"

    @property
    def native_value(self) -> dt.datetime | None:
        return self.coordinator.data.cheapest_window_start


class CheapestWindowEndSensor(_OkSensorBase):
    """When the cheapest contiguous N-hour window ends (start + N hours)."""

    _attr_name = "Cheapest window end"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:clock-end"

    def __init__(self, coordinator: OkChargerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._station_id}_cheapest_end"

    @property
    def native_value(self) -> dt.datetime | None:
        return self.coordinator.data.cheapest_window_end


class CheapestWindowPriceSensor(_OkSensorBase):
    """Average all-in price across the cheapest window, in øre/kWh."""

    _attr_name = "Cheapest window price"
    _attr_native_unit_of_measurement = "øre/kWh"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:cash-clock"

    def __init__(self, coordinator: OkChargerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._station_id}_cheapest_price"

    @property
    def native_value(self) -> int | None:
        return self.coordinator.data.cheapest_window_avg_ore


class ChargingStateSensor(_OkSensorBase):
    """Whether the charger is currently delivering power."""

    _attr_name = "Charging state"
    _attr_icon = "mdi:ev-plug-type2"

    def __init__(self, coordinator: OkChargerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._station_id}_charging_state"

    @property
    def native_value(self) -> str | None:
        station = self.coordinator.data.station
        if station is None:
            return None
        return "charging" if station.is_charging else "idle"
