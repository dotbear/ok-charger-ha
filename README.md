# OK Charger for Home Assistant

Home Assistant integration for **OK home EV chargers** (Peblar units leased through OK a.m.b.a. in Denmark). Adds a real Home Assistant device with a start/stop switch and live spot-price sensors so you can build automations that charge during the cheapest hours of the night — without having to tap the OK app.

> Built originally because the OK app, unlike the older Zaptec/Monta combo, has no automatic cheapest-hour scheduling.

## What it gives you

After setup, your OK-leased charger appears as a Home Assistant device with:

- **Switch: Charge** — start or stop a charging session
- **Sensor: Charging state** — `idle` / `charging`
- **Sensor: Current price** — all-in spot price for the current hour, øre/kWh (DK1 or DK2 depending on your address)
- **Sensor: Cheapest window start** — when today's/tomorrow's cheapest contiguous 4-hour window begins (timestamp)
- **Sensor: Cheapest window price** — average all-in price across that window

That's enough primitives to write a one-automation "charge at the cheapest hours of the night when a car is plugged in" rule.

## Installation

### Via HACS (recommended)

1. Open HACS → Integrations → ⋮ → Custom repositories
2. Add `https://github.com/dotbear/ok-charger-ha` as type "Integration"
3. Install **OK Charger** from the list, restart Home Assistant
4. Settings → Devices & Services → Add Integration → search "OK Charger"
5. Enter the email + password you use for the OK app

### Manual

Copy `custom_components/ok_charger/` into your Home Assistant `config/custom_components/` directory and restart.

## How it works

The OK app talks to two backends: a legacy `okappservice.ok.dk` for account / login, and a modern `appdata.emsp.ok.dk` for charging. Both speak HMAC-signed JSON. The integration:

1. **On setup**: registers a fresh OK device for itself (separate from your phone), then signs in with your credentials. The device id and credentials are stored in Home Assistant.
2. **At runtime**: polls `currentChargings` and `dayAheadPrices` every 30 seconds and 15 minutes respectively, and refreshes the server-side login binding every 10 minutes. Start/stop go directly to the EMSP API.

A useful side effect: this integration logs in as its own "device" with OK, so it doesn't interfere with the OK app on your phone — both can coexist.

## Example automation: charge during the cheapest 4 hours

```yaml
automation:
  - alias: Charge car during cheapest hours
    triggers:
      - trigger: time_pattern
        minutes: /5
    conditions:
      - condition: state
        entity_id: sensor.ok_charger_charging_state
        state: idle
      - condition: template
        value_template: >
          {% set start = states('sensor.ok_charger_cheapest_window_start') %}
          {% set start_dt = as_datetime(start) %}
          {% set now_ = now() %}
          {{ start_dt is not none
             and start_dt <= now_
             and now_ < start_dt + timedelta(hours=4) }}
    actions:
      - action: switch.turn_on
        target:
          entity_id: switch.ok_charger_charge
```

(Pair with a "stop after 4 hours" companion or a battery-state-of-charge condition to taste.)

## Caveats and disclaimer

This integration is **not affiliated with, endorsed by, or supported by OK a.m.b.a.** It works by replicating the OK Android app's API calls. Specifically:

- The auth scheme (HMAC primitives, app secret, app id) was reverse-engineered from the OK Android app v8.0.4. These constants are public — anyone who downloads the APK can read them — but if OK changes the scheme in a future app release, this integration will break until updated.
- OK's terms of service may prohibit automated access. Use this at your own discretion. The author is not a lawyer.
- The integration is read-mostly + charge-control. It does not, and will not, attempt to bypass billing, modify account state, or do anything you couldn't do by tapping buttons in the official app yourself.

If OK reaches out asking for the integration to be modified or taken down, the author will comply.

## Hardware requirements

This was built and tested against a **Peblar 11 kW Type 2** charger leased through OK. It should work for any OK-leased home charger that exposes the `appdata.emsp.ok.dk` `/HomeChargingStation/...` endpoints in their app — that's the same API surface OK uses for all home-charging customers as far as we can tell. PRs welcome if you have a different hardware variant.

## Development

The `tools/` directory contains the synchronous prototype (`ok_client.py`, `ok_hmac.py`) used during reverse-engineering. The HMAC primitives in `custom_components/ok_charger/hmac.py` are byte-equivalent and can be cross-checked against captured app traffic.

## License

MIT — see [LICENSE](LICENSE).
