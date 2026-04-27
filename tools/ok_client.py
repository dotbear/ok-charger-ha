"""Minimal OK app API client for the home charger.

Uses the HMAC scheme reverse-engineered from APK v8.0.4 — see ok_hmac.py.

This is a probe: we don't yet know if OK's backend will accept start-charge
calls from the tablet's deviceId, since the iPhone is the historically-bound
device. The API may simply work, or it may return an error revealing the
binding requirement. Either outcome is informative.
"""

from __future__ import annotations

import json
import sys

import requests

from ok_hmac import legacy_hmac, modern_hmac

# Captured during this session — tablet's identity
DEVICE_ID = "fb556ebd-292b-4cab-ab6b-904d94ff7fd7"
DEVICE_FRIENDLY_ID = "MUWTQW"

# Charger details from /api/v3/HomeChargingStation/location/all
CS_IDENTIFIER = "PBLR-0014962"
CONNECTOR_ID = 1

EMSP_BASE = "https://appdata.emsp.ok.dk"
OKAPP_BASE = "https://okappservice.ok.dk/service/okappservice.svc/v1"


def _now_iso() -> str:
    import datetime
    return datetime.datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")


def _uuid() -> str:
    import uuid
    return str(uuid.uuid4())


def _common_headers() -> dict:
    return {
        "X-App-Version": "8.0.4",
        "X-App-Build-Number": "14294",
        "X-App-Platform": "Android",
        "X-App-Platform-Version": "16",
        "X-App-Hardware-Model": "samsung SM-X216B",
        "X-App-Configuration": "consumer",
        "X-App-Device-Language": "en",
        "X-App-Date": _now_iso(),
        "X-Correlation-ID": _uuid(),
        "Content-Type": "application/json",
        "User-Agent": "okhttp/5.3.2",
        "Accept-Encoding": "gzip",
    }


def emsp_request(method: str, path: str, body: dict | None = None) -> requests.Response:
    """Sign and send a request to the EMSP API (header-based HMAC-SHA256).

    The home-charging Retrofit factory passes z=false to the uq.l interceptor,
    which makes it NOT include the body in HMAC input — only deviceId+timestamp.
    """
    sig, ts = modern_hmac(None, DEVICE_ID, add_device_id=True)
    headers = {
        **_common_headers(),
        "OK-App-DeviceId": DEVICE_ID,
        "OK-App-Hmac-Signature": sig,
        "OK-App-Hmac-Timestamp": str(ts),
    }
    url = f"{EMSP_BASE}{path}"
    if method == "GET":
        return requests.get(url, headers=headers, timeout=15)
    if method == "POST":
        return requests.post(url, headers=headers, data=json.dumps(body) if body else None, timeout=15)
    if method == "DELETE":
        return requests.delete(url, headers=headers, timeout=15)
    if method == "PUT":
        return requests.put(url, headers=headers, data=json.dumps(body) if body else None, timeout=15)
    raise ValueError(method)


def okapp_request(path: str, body: dict) -> requests.Response:
    """Send a legacy okappservice.ok.dk request, signing the body with HMAC-SHA1."""
    body = dict(body)
    body["hmac"] = legacy_hmac(body)
    headers = _common_headers()
    return requests.post(f"{OKAPP_BASE}{path}", headers=headers, data=json.dumps(body), timeout=15)


def legacy_login(email: str, password: str) -> dict:
    """Re-establish a logged-in session against okappservice.ok.dk.

    Returns the LogIndResult dict (contains LogIndToken). Side effect: the
    server-side binding between DEVICE_ID and the user is refreshed, which
    appears to be what EMSP write endpoints check.
    """
    body = {
        "deviceId": DEVICE_ID,
        "emailadresse": email,
        "kodeord": password,
    }
    r = okapp_request("/logind", body)
    r.raise_for_status()
    return r.json().get("LogIndResult", {})


def list_stations():
    return emsp_request("GET", "/api/v3/HomeChargingStation/location/all")


def current_chargings():
    return emsp_request("GET", "/api/v2/HomeChargingStation/currentChargings")


def day_ahead_prices(cs_id: str = CS_IDENTIFIER):
    return emsp_request("GET", f"/api/v3/HomeChargingStation/dayAheadPrices/{cs_id.lower()}")


def start_charge():
    body = {
        "chargingStationId": CS_IDENTIFIER,
        "connectorId": CONNECTOR_ID,
        "friendlyDeviceId": DEVICE_FRIENDLY_ID,
        "scheduledStart": None,
        "scheduledEnd": None,
    }
    return emsp_request("POST", "/api/v2/HomeChargingStation/start", body)


def stop_charge(charging_token: str):
    body = {"chargingToken": charging_token}
    return emsp_request("POST", "/api/v2/HomeChargingStation/stop", body)


def show(label: str, r: requests.Response):
    print(f"=== {label}: {r.status_code} ===")
    ct = r.headers.get("content-type", "")
    if "json" in ct:
        try:
            print(json.dumps(r.json(), indent=2, ensure_ascii=False)[:2000])
        except Exception:
            print(r.text[:2000])
    else:
        print(r.text[:2000])
    print()


if __name__ == "__main__":
    import os
    cmd = sys.argv[1] if len(sys.argv) > 1 else "probe"

    if cmd == "probe":
        show("list_stations", list_stations())
        show("current_chargings", current_chargings())
        show("day_ahead_prices", day_ahead_prices())
    elif cmd == "login":
        email = os.environ["OK_EMAIL"]
        pw = os.environ["OK_PASSWORD"]
        result = legacy_login(email, pw)
        print(json.dumps({k: v for k, v in result.items() if k != "KundeAdresse"}, indent=2, ensure_ascii=False))
    elif cmd == "start":
        # If credentials are present in env, refresh session first
        if "OK_EMAIL" in os.environ and "OK_PASSWORD" in os.environ:
            print("Refreshing session via legacy login...")
            r = legacy_login(os.environ["OK_EMAIL"], os.environ["OK_PASSWORD"])
            print(f"  ok, brugernr={r.get('Brugernr')}\n")
        show("start_charge", start_charge())
    elif cmd == "stop":
        if len(sys.argv) < 3:
            print("usage: ok_client.py stop <chargingToken>")
            sys.exit(1)
        show(f"stop_charge {sys.argv[2]}", stop_charge(sys.argv[2]))
    else:
        print(f"unknown command: {cmd}")
