"""Data update coordinator for the OK Charger integration."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from dataclasses import dataclass, field
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import OkApiError, OkAuthError, OkChargerClient
from .const import (
    CHARGE_DEADLINE_HOUR,
    DEFAULT_CHEAP_HOURS,
    DOMAIN,
    PRICE_REFRESH_INTERVAL,
    SCAN_INTERVAL,
    SESSION_REFRESH_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class ChargingStationState:
    """Snapshot of a single charging station's state."""

    cs_identifier: str
    location_id: str
    name: str
    serial_number: str
    model: str
    firmware_version: str
    vendor: str
    connector_id: int
    connector_power_kw: int
    auto_start: bool
    # Live state (filled from currentChargings)
    is_charging: bool = False
    charging_token: str | None = None


def device_info_for(station: ChargingStationState) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, station.cs_identifier)},
        manufacturer=station.vendor or "Peblar",
        model=station.model,
        sw_version=station.firmware_version,
        name=station.name,
        serial_number=station.serial_number,
    )


@dataclass
class HourlyPrice:
    applicable_time: dt.datetime
    total_ore_per_kwh: int  # all-in including VAT, transmission tariff, and electricity tax

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> HourlyPrice:
        # The three "*IncludingVat" fields are the all-in customer price
        # components. The standalone "vat" field is the VAT portion already
        # baked into electricityPriceIncludingVat — broken out only for the
        # OK app's price-breakdown UI. Including it here double-counts VAT
        # (verified against the OK app's displayed prices on 2026-04-27).
        return cls(
            applicable_time=dt.datetime.fromisoformat(raw["applicableTime"]),
            total_ore_per_kwh=(
                raw.get("tariffIncludingVat", 0)
                + raw.get("electricityTaxIncludingVat", 0)
                + raw.get("electricityPriceIncludingVat", 0)
            ),
        )


@dataclass
class CoordinatorData:
    station: ChargingStationState | None = None
    prices: list[HourlyPrice] = field(default_factory=list)
    cheapest_window_start: dt.datetime | None = None
    cheapest_window_end: dt.datetime | None = None
    cheapest_window_avg_ore: int | None = None
    last_session_refresh: dt.datetime | None = None
    last_price_refresh: dt.datetime | None = None


class OkChargerCoordinator(DataUpdateCoordinator[CoordinatorData]):
    """Refreshes charger state on a fast cadence; prices + login on slower cadences."""

    def __init__(self, hass: HomeAssistant, client: OkChargerClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )
        self.client = client
        self._lock = asyncio.Lock()
        self._data = CoordinatorData()

    async def _maybe_refresh_session(self) -> None:
        now = dt.datetime.now(tz=dt.timezone.utc)
        last = self._data.last_session_refresh
        if last is not None and now - last < SESSION_REFRESH_INTERVAL:
            return
        try:
            await self.client.refresh_session()
            self._data.last_session_refresh = now
        except OkAuthError as exc:
            raise UpdateFailed(f"Login refresh rejected: {exc}") from exc

    async def _maybe_refresh_prices(self) -> None:
        if self._data.station is None:
            return
        now = dt.datetime.now(tz=dt.timezone.utc)
        last = self._data.last_price_refresh
        if last is not None and now - last < PRICE_REFRESH_INTERVAL:
            return
        raw = await self.client.day_ahead_prices(self._data.station.cs_identifier)
        prices = [HourlyPrice.from_api(p) for p in raw.get("prices", [])]
        self._data.prices = prices
        self._data.last_price_refresh = now
        self._compute_cheap_window()

    def _compute_cheap_window(self, hours: int = DEFAULT_CHEAP_HOURS) -> None:
        prices = self._data.prices
        self._data.cheapest_window_start = None
        self._data.cheapest_window_end = None
        self._data.cheapest_window_avg_ore = None

        if len(prices) < hours:
            return

        now = dt.datetime.now(tz=dt.timezone.utc)
        local_now = now.astimezone()
        deadline_local = local_now.replace(
            hour=CHARGE_DEADLINE_HOUR, minute=0, second=0, microsecond=0
        )
        if deadline_local <= local_now:
            deadline_local += dt.timedelta(days=1)
        deadline = deadline_local.astimezone(dt.timezone.utc)

        # Eligible price hours: start strictly in the future (the automation's
        # time trigger can't fire on a moment that's already passed), and end
        # at or before the deadline.
        one_hour = dt.timedelta(hours=1)
        eligible = [
            p
            for p in prices
            if p.applicable_time > now
            and p.applicable_time + one_hour <= deadline
        ]
        if len(eligible) < hours:
            return

        best_idx = min(
            range(len(eligible) - hours + 1),
            key=lambda i: sum(p.total_ore_per_kwh for p in eligible[i : i + hours]),
        )
        window = eligible[best_idx : best_idx + hours]
        self._data.cheapest_window_start = window[0].applicable_time
        self._data.cheapest_window_end = window[-1].applicable_time + one_hour
        self._data.cheapest_window_avg_ore = round(
            sum(p.total_ore_per_kwh for p in window) / hours
        )

    async def _refresh_station(self) -> None:
        stations = await self.client.list_stations()
        # We only support a single charger today — pick the first one we find.
        for loc in stations:
            for cs in loc.get("chargingStations", []):
                connector = (cs.get("connectors") or [{}])[0]
                self._data.station = ChargingStationState(
                    cs_identifier=cs["csIdentifier"],
                    location_id=loc["locationId"],
                    name=cs.get("name") or cs["csIdentifier"],
                    serial_number=cs.get("serialNumber", ""),
                    model=cs.get("model", ""),
                    firmware_version=cs.get("firmwareVersion", ""),
                    vendor=cs.get("vendor", ""),
                    connector_id=connector.get("connectorId", 1),
                    connector_power_kw=connector.get("power", 0),
                    auto_start=cs.get("autoStart", False),
                )
                return
        self._data.station = None

    async def _refresh_charging_state(self) -> None:
        if self._data.station is None:
            return
        active = await self.client.current_chargings()
        self._data.station.is_charging = False
        self._data.station.charging_token = None
        for entry in active:
            if entry.get("csIdentifier") == self._data.station.cs_identifier:
                self._data.station.is_charging = True
                self._data.station.charging_token = entry.get("chargingToken")
                break

    async def _async_update_data(self) -> CoordinatorData:
        async with self._lock:
            try:
                if self._data.station is None:
                    await self._refresh_station()
                await self._maybe_refresh_session()
                await self._refresh_charging_state()
                await self._maybe_refresh_prices()
            except OkApiError as exc:
                raise UpdateFailed(str(exc)) from exc
        return self._data

    async def async_start_charge(self) -> str:
        """Trigger a start. Returns the chargingToken."""
        if self._data.station is None:
            raise UpdateFailed("No charging station available")
        async with self._lock:
            try:
                await self._maybe_refresh_session()
                station = self._data.station
                resp = await self.client.start_charge(station.cs_identifier, station.connector_id)
            except OkApiError as exc:
                raise UpdateFailed(str(exc)) from exc
        token = resp.get("chargingToken")
        if not token:
            raise UpdateFailed(f"start_charge returned no chargingToken: {resp!r}")
        # Optimistic state update; the next poll will confirm.
        self._data.station.is_charging = True
        self._data.station.charging_token = token
        self.async_set_updated_data(self._data)
        return token

    async def async_stop_charge(self) -> None:
        if self._data.station is None or not self._data.station.charging_token:
            return
        async with self._lock:
            try:
                await self._maybe_refresh_session()
                await self.client.stop_charge(self._data.station.charging_token)
            except OkApiError as exc:
                raise UpdateFailed(str(exc)) from exc
        self._data.station.is_charging = False
        self._data.station.charging_token = None
        self.async_set_updated_data(self._data)
