"""Async client for the OK app API.

This is a non-blocking aiohttp port of tools/ok_client.py — see that file
for the synchronous reference implementation. All HMAC details are in
hmac.py and were reverse-engineered from OK Android v8.0.4.

API surface used by this integration:

    okappservice.ok.dk           (legacy, SHA1-in-body HMAC)
        POST /v1/RegistrerDevice — provision a deviceId for this install
        POST /v1/logind          — refresh server-side device→user binding

    appdata.emsp.ok.dk           (modern, SHA256 header HMAC)
        GET  /api/v3/HomeChargingStation/location/all
        GET  /api/v2/HomeChargingStation/currentChargings
        GET  /api/v2/HomeChargingStation/quickreceipt/{token}
        GET  /api/v3/HomeChargingStation/dayAheadPrices/{cs}
        POST /api/v2/HomeChargingStation/start
        POST /api/v2/HomeChargingStation/stop
        POST /api/v2/HomeChargingStation/setAutostart
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import uuid
from typing import Any

import aiohttp

from .const import (
    APP_BUILD_NUMBER,
    APP_HARDWARE_MODEL,
    APP_PLATFORM,
    APP_PLATFORM_VERSION,
    APP_VERSION,
    EMSP_BASE,
    OKAPP_BASE,
    USER_AGENT,
)
from .hmac import legacy_hmac, modern_hmac

_LOGGER = logging.getLogger(__name__)


class OkApiError(Exception):
    """Base error for OK API failures."""


class OkAuthError(OkApiError):
    """Raised when the OK API rejects the request as unauthenticated."""


class OkChargerError(OkApiError):
    """Raised when a charger-control call fails for a non-auth reason."""


def _now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _common_headers() -> dict[str, str]:
    return {
        "X-App-Version": APP_VERSION,
        "X-App-Build-Number": APP_BUILD_NUMBER,
        "X-App-Platform": APP_PLATFORM,
        "X-App-Platform-Version": APP_PLATFORM_VERSION,
        "X-App-Hardware-Model": APP_HARDWARE_MODEL,
        "X-App-Configuration": "consumer",
        "X-App-Device-Language": "en",
        "X-App-Date": _now_iso(),
        "X-Correlation-ID": str(uuid.uuid4()),
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
        "Accept-Encoding": "gzip",
    }


class OkChargerClient:
    """Async client wrapping the OK home-charging API.

    Stateful: holds the deviceId after registration, tracks the user number
    after logind. The coordinator is responsible for periodically calling
    `refresh_session()` to keep the server-side device→user binding alive
    (write endpoints reject with errorcode 200010 if it lapses).
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        email: str,
        password: str,
        app_id: str,
        device_id: str | None = None,
        device_friendly_id: str | None = None,
    ) -> None:
        self._session = session
        self._email = email
        self._password = password
        self._app_id = app_id
        self._device_id = device_id
        self._device_friendly_id = device_friendly_id
        self._user_number: int | None = None

    @property
    def device_id(self) -> str | None:
        return self._device_id

    @property
    def device_friendly_id(self) -> str | None:
        return self._device_friendly_id

    @property
    def user_number(self) -> int | None:
        return self._user_number

    async def register_device(self) -> tuple[str, str]:
        """Provision a fresh deviceId on the OK backend for this install.

        Returns (device_id, device_friendly_id). Tied to this install's
        unique app_id — a previously-registered app_id will be rejected
        with errorcode 10036.
        """
        os_device_token = str(uuid.uuid4())
        body = {
            "appId": self._app_id,
            "osDeviceToken": os_device_token,
        }
        result = await self._okapp_post("/RegistrerDevice", body)
        register = result.get("RegistrerDeviceResult") or {}
        device_id = register.get("DeviceId")
        friendly = register.get("DeviceFriendlyId")
        if not device_id or not friendly:
            raise OkApiError(f"RegistrerDevice returned no DeviceId: {result!r}")
        self._device_id = device_id
        self._device_friendly_id = friendly
        return device_id, friendly

    async def refresh_session(self) -> int:
        """Re-establish the server-side device→user binding.

        Required before any write endpoint on the EMSP service. Returns the
        Brugernr (user number).
        """
        if not self._device_id:
            raise OkApiError("refresh_session called before register_device")
        body = {
            "deviceId": self._device_id,
            "emailadresse": self._email,
            "kodeord": self._password,
        }
        result = await self._okapp_post("/logind", body)
        login = result.get("LogIndResult") or {}
        user_number = login.get("Brugernr")
        if not user_number:
            raise OkAuthError(f"Login failed: {result!r}")
        self._user_number = user_number
        if not self._device_friendly_id:
            # Some flows need DeviceFriendlyId after login; populate from
            # device-list endpoint if not already known.
            self._device_friendly_id = login.get("DeviceFriendlyId")
        return user_number

    async def list_stations(self) -> list[dict[str, Any]]:
        return await self._emsp_request("GET", "/api/v3/HomeChargingStation/location/all")

    async def current_chargings(self) -> list[dict[str, Any]]:
        return await self._emsp_request("GET", "/api/v2/HomeChargingStation/currentChargings")

    async def day_ahead_prices(self, cs_identifier: str) -> dict[str, Any]:
        path = f"/api/v3/HomeChargingStation/dayAheadPrices/{cs_identifier.lower()}"
        return await self._emsp_request("GET", path)

    async def quick_receipt(self, charging_token: str) -> dict[str, Any]:
        path = f"/api/v2/HomeChargingStation/quickreceipt/{charging_token}"
        return await self._emsp_request("GET", path)

    async def start_charge(self, cs_identifier: str, connector_id: int) -> dict[str, Any]:
        if not self._device_friendly_id:
            raise OkApiError("start_charge requires device_friendly_id (call refresh_session first)")
        body = {
            "chargingStationId": cs_identifier,
            "connectorId": connector_id,
            "friendlyDeviceId": self._device_friendly_id,
            "scheduledStart": None,
            "scheduledEnd": None,
        }
        return await self._emsp_request("POST", "/api/v2/HomeChargingStation/start", body)

    async def stop_charge(self, charging_token: str) -> dict[str, Any]:
        body = {"chargingToken": charging_token}
        return await self._emsp_request("POST", "/api/v2/HomeChargingStation/stop", body)

    async def set_autostart(self, cs_identifier: str, enabled: bool) -> dict[str, Any]:
        body = {"chargingStationId": cs_identifier, "autoStart": enabled}
        return await self._emsp_request("POST", "/api/v2/HomeChargingStation/setAutostart", body)

    async def _okapp_post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        body = dict(body)
        body["hmac"] = legacy_hmac(body, self._app_id)
        url = f"{OKAPP_BASE}{path}"
        async with self._session.post(
            url, headers=_common_headers(), data=json.dumps(body), timeout=aiohttp.ClientTimeout(total=15)
        ) as resp:
            text = await resp.text()
            if resp.status >= 400:
                raise OkApiError(f"{path} returned {resp.status}: {text[:200]}")
            try:
                return json.loads(text)
            except ValueError as exc:
                raise OkApiError(f"{path} returned non-JSON: {text[:200]}") from exc

    async def _emsp_request(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> Any:
        if not self._device_id:
            raise OkApiError("_emsp_request requires deviceId (call register_device first)")
        sig, ts = modern_hmac(self._device_id, self._app_id)
        headers = {
            **_common_headers(),
            "OK-App-DeviceId": self._device_id,
            "OK-App-Hmac-Signature": sig,
            "OK-App-Hmac-Timestamp": str(ts),
        }
        url = f"{EMSP_BASE}{path}"
        data = json.dumps(body) if body is not None else None
        async with self._session.request(
            method,
            url,
            headers=headers,
            data=data,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            text = await resp.text()
            if resp.status == 400 and "200010" in text:
                raise OkAuthError("EMSP rejected with errorcode 200010 — session expired")
            if resp.status >= 400:
                raise OkApiError(f"{method} {path} returned {resp.status}: {text[:200]}")
            if not text:
                return {}
            try:
                return json.loads(text)
            except ValueError as exc:
                raise OkApiError(f"{method} {path} returned non-JSON: {text[:200]}") from exc


