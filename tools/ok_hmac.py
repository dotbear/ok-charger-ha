"""OK app HMAC implementation, reverse-engineered from APK v8.0.4.

Two schemes used by the OK Android app:

- LEGACY (okappservice.ok.dk):
    - Algorithm: HMAC-SHA1
    - Key: appSecret + appId
    - Input: canonicalized JSON body
    - Result: hex digest, added as "hmac" property in the body

- MODERN (appdata.emsp.ok.dk, *.okcloud.dk):
    - Algorithm: HMAC-SHA256
    - Key: appSecret + appId  (same key)
    - Input: canonicalized JSON body, with extra "timestamp" (and optionally "deviceId")
        - For GET/DELETE: just {"timestamp": ts, "deviceId": did}
        - For POST/PUT: original body + timestamp + deviceId
    - Result: hex digest, added as "OK-App-Hmac-Signature" header
        - "OK-App-DeviceId", "OK-App-Hmac-Timestamp" headers also added

Canonicalization (b9.m8 in source):
    - Sort body's top-level entries by key (Danish locale, case-insensitive primary strength)
    - Wrap each entry in its own single-key JsonObject
    - Serialize the array with Gson, HTML escaping disabled
    - Replace "/" with "\\/" in the result
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time

# Hardcoded in dk.shape.okbilistkit.okapi.auth.OKHMACConstants
APP_SECRET = "49BA6A36-956A-4444-8B7B-C04DD63D200F"

# Hardcoded for OK consumer Android app v8.0.4 (from RegistrerDevice request)
APP_ID = "1088dea6-4822-42b1-af12-4efb9602425d"


def _hmac_key(app_id: str = APP_ID) -> bytes:
    return (APP_SECRET + app_id).encode("utf-8")


def _danish_sort_key(s: str) -> tuple:
    """Approximate Java Collator(da_DK, strength=PRIMARY).
    Primary strength = ignore case AND accent differences. For Danish,
    'å','ä' sort together at the end as 'a-ring'. For typical API field names
    (alphanumeric ASCII), case-insensitive ASCII sort is sufficient.
    """
    return s.lower()


def canonicalize(obj: dict) -> str:
    """Reproduce m8.c(m8.b(jsonObject, Locale.da_DK))."""
    # Sort by key, Danish locale primary strength
    items = sorted(obj.items(), key=lambda kv: _danish_sort_key(kv[0]))
    # Wrap each in its own object, put in array
    array = [{k: v} for k, v in items]
    # Gson serialization style: no spaces, HTML escaping disabled, and Java's escape /
    # json.dumps already doesn't escape '/' by default — Gson's default DOES, then m8.c re-replaces.
    # Net result: forward slashes get escaped as "\/" in the canonical string.
    s = json.dumps(array, separators=(",", ":"), ensure_ascii=False)
    s = s.replace("/", "\\/")
    return s


def legacy_hmac(body: dict, app_id: str = APP_ID) -> str:
    """HMAC-SHA1 hex digest for okappservice.ok.dk requests.

    Returns just the hex digest. Caller is responsible for adding it as
    `body["hmac"] = <digest>` before serializing.
    """
    canonical = canonicalize({k: v for k, v in body.items() if k != "hmac"})
    mac = hmac.new(_hmac_key(app_id), canonical.encode("utf-8"), hashlib.sha1)
    return mac.hexdigest()


def modern_hmac(
    body: dict | None,
    device_id: str,
    timestamp: int | None = None,
    add_device_id: bool = True,
    app_id: str = APP_ID,
) -> tuple[str, int]:
    """HMAC-SHA256 hex digest for appdata.emsp.ok.dk and *.okcloud.dk.

    Returns (signature_hex, timestamp). Caller adds the headers:
        OK-App-DeviceId: <device_id>
        OK-App-Hmac-Timestamp: <timestamp>
        OK-App-Hmac-Signature: <signature_hex>
    """
    if timestamp is None:
        timestamp = int(time.time())
    signed = dict(body or {})
    signed["timestamp"] = timestamp
    if add_device_id and "deviceId" not in signed:
        signed["deviceId"] = device_id
    canonical = canonicalize(signed)
    mac = hmac.new(_hmac_key(app_id), canonical.encode("utf-8"), hashlib.sha256)
    return mac.hexdigest(), timestamp


# Self-test against captured flows
if __name__ == "__main__":
    # RegistrerDevice request from flows.mitm at 13:43:32
    captured_body = {
        "appId": "1088dea6-4822-42b1-af12-4efb9602425d",
        "osDeviceToken": "ba067f20-7ad1-3102-98e8-a6073f5bfbe2",
    }
    expected_hmac = "4aeb6681db1d4c4bb58a2f6f2c162c11e7097361"
    canonical = canonicalize(captured_body)
    print(f"Canonical: {canonical}")
    actual = legacy_hmac(captured_body)
    print(f"Expected SHA1 hmac: {expected_hmac}")
    print(f"Actual   SHA1 hmac: {actual}")
    print(f"MATCH: {actual == expected_hmac}")
    print()

    # EMSP /api/v3/HomeChargingStation/location/all at 13:43:32 (GET, no body)
    expected_sig = "20a5b5425e938291c8a0aac21a7eb0c65a1e289f773d9951c809a88ce7ad301f"
    expected_ts = 1777290298
    device_id = "fb556ebd-292b-4cab-ab6b-904d94ff7fd7"
    sig, _ = modern_hmac(None, device_id, timestamp=expected_ts, add_device_id=True)
    print(f"Expected SHA256 sig: {expected_sig}")
    print(f"Actual   SHA256 sig: {sig}")
    print(f"MATCH: {sig == expected_sig}")
