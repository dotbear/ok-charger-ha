"""The OK Charger integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import OkApiError, OkChargerClient
from .const import CONF_APP_ID, CONF_DEVICE_ID, CONF_EMAIL, CONF_PASSWORD, DOMAIN
from .coordinator import OkChargerCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.SWITCH]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up OK Charger from a config entry."""
    session = async_get_clientsession(hass)
    client = OkChargerClient(
        session=session,
        email=entry.data[CONF_EMAIL],
        password=entry.data[CONF_PASSWORD],
        app_id=entry.data[CONF_APP_ID],
        device_id=entry.data[CONF_DEVICE_ID],
    )

    coordinator = OkChargerCoordinator(hass, client)
    try:
        await coordinator.async_config_entry_first_refresh()
    except OkApiError as exc:
        raise ConfigEntryNotReady(str(exc)) from exc

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
