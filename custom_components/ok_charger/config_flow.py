"""Config flow for the OK Charger integration."""

from __future__ import annotations

import logging
import uuid
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import OkApiError, OkAuthError, OkChargerClient
from .const import (
    CONF_APP_ID,
    CONF_DEVICE_FRIENDLY_ID,
    CONF_DEVICE_ID,
    CONF_EMAIL,
    CONF_PASSWORD,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class OkChargerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Walk the user through linking their OK account."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                user_number, creds = await self._validate(
                    user_input[CONF_EMAIL], user_input[CONF_PASSWORD]
                )
            except OkAuthError:
                errors["base"] = "invalid_auth"
            except OkApiError as exc:
                _LOGGER.exception("OK API error during setup: %s", exc)
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(f"ok_charger_{user_number}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=user_input[CONF_EMAIL],
                    data={**user_input, **creds},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=USER_SCHEMA,
            errors=errors,
        )

    async def _validate(self, email: str, password: str) -> tuple[int, dict[str, Any]]:
        session = async_get_clientsession(self.hass)
        app_id = str(uuid.uuid4())
        client = OkChargerClient(
            session=session, email=email, password=password, app_id=app_id
        )
        device_id, friendly = await client.register_device()
        user_number = await client.refresh_session()
        return user_number, {
            CONF_APP_ID: app_id,
            CONF_DEVICE_ID: device_id,
            CONF_DEVICE_FRIENDLY_ID: friendly,
        }
