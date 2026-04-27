"""Constants for the OK Charger integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "ok_charger"

# Hardcoded in dk.shape.okbilistkit.okapi.auth.OKHMACConstants — same secret
# baked into every public OK Android APK. Combined with the per-install
# appId to derive each install's HMAC key.
APP_SECRET = "49BA6A36-956A-4444-8B7B-C04DD63D200F"

# The app_id is NOT a global constant: AppIdentificationComponent in the OK
# app generates a fresh UUID on first launch, persists it under the prefs
# key "AppIdentificationComponentAppID", and reuses it for the install's
# lifetime. The OK backend rejects RegistrerDevice with errorcode 10036 if
# the same appId tries to register twice — so each HA install must mint its
# own UUID at config-flow time and store it in the config entry.
CONF_APP_ID = "app_id"

# API base URLs (production)
OKAPP_BASE = "https://okappservice.ok.dk/service/okappservice.svc/v1"
EMSP_BASE = "https://appdata.emsp.ok.dk"

# App identification headers — kept for backend compatibility
APP_VERSION = "8.0.4"
APP_BUILD_NUMBER = "14294"
APP_PLATFORM = "Android"
APP_PLATFORM_VERSION = "16"
APP_HARDWARE_MODEL = "Home Assistant"
USER_AGENT = "okhttp/5.3.2"

# Config keys
CONF_EMAIL = "email"
CONF_PASSWORD = "password"
CONF_DEVICE_ID = "device_id"
CONF_DEVICE_FRIENDLY_ID = "device_friendly_id"
CONF_USER_NUMBER = "user_number"

# Coordinator timing
SCAN_INTERVAL = timedelta(seconds=30)
PRICE_REFRESH_INTERVAL = timedelta(minutes=15)
SESSION_REFRESH_INTERVAL = timedelta(minutes=10)

# How many cheapest hours to find for the auto-charge sensor
DEFAULT_CHEAP_HOURS = 4
