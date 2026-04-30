"""HMAC signing primitives for the OK app API.

Reverse-engineered from OK Android v8.0.4. There are two schemes:

1. LEGACY (okappservice.ok.dk): HMAC-SHA1 over a canonicalized JSON body,
   result added back to the body as the `hmac` property.

2. MODERN (appdata.emsp.ok.dk): HMAC-SHA256 over a canonicalized JSON object
   containing only `deviceId` + `timestamp` (the request body is NOT covered
   by the signature for the home-charging Retrofit factory, since it's
   constructed with z=false in qq.h.a()). Result is sent as headers
   `OK-App-Hmac-Signature`, `OK-App-Hmac-Timestamp`, `OK-App-DeviceId`.

Canonicalization (b9.m8 in source): sort entries by key with a Danish-locale
collator at primary strength (case-insensitive ASCII for our purposes), wrap
each entry in a single-key JsonObject, serialize the resulting array with
HTML-escaping disabled, and replace `/` with `\\/` in the output.

Both schemes use the same HMAC key: APP_SECRET + APP_ID.
"""

from __future__ import annotations

import hashlib
import hmac as _hmac
import json
import time
from functools import lru_cache
from typing import Any

from .const import APP_SECRET


@lru_cache(maxsize=1)
def _hmac_key(app_id: str) -> bytes:
    return (APP_SECRET + app_id).encode("utf-8")


def _danish_sort_key(s: str) -> str:
    """Approximation of Java Collator(da_DK, PRIMARY).

    For our usage — alphanumeric ASCII keys like `chargingStationId`,
    `deviceId`, `timestamp` — case-insensitive ASCII sort is exact.
    """
    return s.lower()


def canonicalize(obj: dict[str, Any]) -> str:
    """Reproduce m8.c(m8.b(jsonObject, Locale.da_DK))."""
    items = sorted(obj.items(), key=lambda kv: _danish_sort_key(kv[0]))
    array = [{k: v} for k, v in items]
    serialized = json.dumps(array, separators=(",", ":"), ensure_ascii=False)
    return serialized.replace("/", "\\/")


def legacy_hmac(body: dict[str, Any], app_id: str) -> str:
    """HMAC-SHA1 hex digest for okappservice.ok.dk requests.

    Caller must add the result as `body["hmac"] = <digest>` before serializing.
    """
    payload = {k: v for k, v in body.items() if k != "hmac"}
    canonical = canonicalize(payload)
    mac = _hmac.new(_hmac_key(app_id), canonical.encode("utf-8"), hashlib.sha1)
    return mac.hexdigest()


def modern_hmac(
    device_id: str,
    app_id: str,
    timestamp: int | None = None,
) -> tuple[str, int]:
    """HMAC-SHA256 hex digest for appdata.emsp.ok.dk requests.

    The home-charging service is constructed with z=false, which causes the
    request body to be excluded from the HMAC input. Only deviceId and
    timestamp are signed.

    Returns (signature_hex, timestamp).
    """
    if timestamp is None:
        timestamp = int(time.time())
    signed = {"deviceId": device_id, "timestamp": timestamp}
    canonical = canonicalize(signed)
    mac = _hmac.new(_hmac_key(app_id), canonical.encode("utf-8"), hashlib.sha256)
    return mac.hexdigest(), timestamp
