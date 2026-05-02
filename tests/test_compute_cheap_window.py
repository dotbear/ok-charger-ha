"""Tests for OkChargerCoordinator._compute_cheap_window.

These cover the two regressions diagnosed from production traces on
2026-04-30 / 2026-05-01:

1. The window slid forward by an hour every price refresh, causing the
   `at: sensor.cheapest_window_start` time trigger to fire repeatedly and
   producing a string of 5-minute spurious charge sessions.
2. Recomputing mid-window overwrote the in-flight stop time, leaving a
   16-hour unintentional charge that only ended because the car's own
   battery cutoff kicked in.

Both are addressed by holding the previously-chosen window stable until
its end has passed (day-ahead prices are immutable, so re-optimizing
mid-cycle has no upside).
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# Load coordinator.py directly, bypassing custom_components/ok_charger/__init__.py
# (which imports a long tail of HA modules we don't need for unit tests).
_coordinator_path = REPO_ROOT / "custom_components" / "ok_charger" / "coordinator.py"
_const_path = REPO_ROOT / "custom_components" / "ok_charger" / "const.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# const has no external deps; load it under the name coordinator.py expects.
_const = _load("custom_components.ok_charger.const", _const_path)
# Also satisfy `from .api import ...` and `from .const import ...` (relative imports
# within coordinator.py). Both are referenced via the package path.
sys.modules.setdefault("custom_components", type(sys)("custom_components"))
sys.modules["custom_components"].__path__ = [str(REPO_ROOT / "custom_components")]
sys.modules.setdefault("custom_components.ok_charger", type(sys)("custom_components.ok_charger"))
sys.modules["custom_components.ok_charger"].__path__ = [
    str(REPO_ROOT / "custom_components" / "ok_charger")
]
# Stub the api module — coordinator only references the exception types and the
# client class as a typing parameter, none of which we exercise here.
_api_stub = type(sys)("custom_components.ok_charger.api")


class _OkApiError(Exception):
    pass


class _OkAuthError(_OkApiError):
    pass


class _OkChargerClient:
    pass


_api_stub.OkApiError = _OkApiError
_api_stub.OkAuthError = _OkAuthError
_api_stub.OkChargerClient = _OkChargerClient
sys.modules["custom_components.ok_charger.api"] = _api_stub
sys.modules["custom_components.ok_charger.const"] = _const

coordinator_mod = _load("custom_components.ok_charger.coordinator", _coordinator_path)
CoordinatorData = coordinator_mod.CoordinatorData
HourlyPrice = coordinator_mod.HourlyPrice
OkChargerCoordinator = coordinator_mod.OkChargerCoordinator

UTC = dt.timezone.utc


def _coordinator() -> OkChargerCoordinator:
    """Build a coordinator without running its real __init__ (which expects HA)."""
    inst = OkChargerCoordinator.__new__(OkChargerCoordinator)
    inst._data = CoordinatorData()
    return inst


def _price(hour_utc: dt.datetime, ore: int) -> HourlyPrice:
    return HourlyPrice(applicable_time=hour_utc, total_ore_per_kwh=ore)


def _prices_for_24h(start: dt.datetime, schedule: list[int]) -> list[HourlyPrice]:
    """Build N hourly prices starting at `start` with the given øre values."""
    return [_price(start + dt.timedelta(hours=i), p) for i, p in enumerate(schedule)]


class _Clock:
    """Holder for the fake current time. Tests reassign `value` to advance it."""
    value: dt.datetime = dt.datetime(2026, 5, 1, 12, 0, tzinfo=UTC)


class _FakeDateTime(dt.datetime):
    """Drop-in for `datetime.datetime` with `.now()` driven by `_Clock.value`.

    Subclassing means every other constructor and classmethod (fromisoformat,
    .replace, arithmetic) keeps working unchanged."""

    @classmethod
    def now(cls, tz=None):
        v = _Clock.value
        return v if tz is not None else v.replace(tzinfo=None)


@pytest.fixture
def fixed_now():
    """Pin `now()` inside coordinator.py and the system tz to Europe/Copenhagen
    so deadline math is reproducible regardless of where the test runs."""
    saved_tz = os.environ.get("TZ")
    os.environ["TZ"] = "Europe/Copenhagen"
    import time as _time
    if hasattr(_time, "tzset"):
        _time.tzset()

    _Clock.value = dt.datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
    real_dt = coordinator_mod.dt.datetime
    coordinator_mod.dt.datetime = _FakeDateTime
    try:
        yield _Clock
    finally:
        coordinator_mod.dt.datetime = real_dt
        if saved_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = saved_tz
        if hasattr(_time, "tzset"):
            _time.tzset()


def test_no_prices_yields_none(fixed_now):
    c = _coordinator()
    c._compute_cheap_window()
    assert c._data.cheapest_window_start is None
    assert c._data.cheapest_window_end is None
    assert c._data.cheapest_window_avg_ore is None


def test_fewer_prices_than_window_size(fixed_now):
    c = _coordinator()
    # Only 2 future hours available; default window is 4 hours.
    c._data.prices = _prices_for_24h(dt.datetime(2026, 5, 1, 13, tzinfo=UTC), [100, 90])
    c._compute_cheap_window()
    assert c._data.cheapest_window_start is None


def test_picks_cheapest_contiguous_window(fixed_now):
    """now=12:00 UTC May 1 (= 14:00 CPH); deadline = May 2 08:00 CPH = May 2 06:00 UTC."""
    c = _coordinator()
    # Hours 13:00..05:00 UTC available (17 hours). The cheapest 4-hour run is
    # hours 02:00..05:00 UTC at 50 øre each.
    schedule = [
        # 13:00..23:00 UTC May 1
        200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200,
        # 00:00..05:00 UTC May 2
        100, 100, 50, 50, 50, 50,
    ]
    c._data.prices = _prices_for_24h(dt.datetime(2026, 5, 1, 13, tzinfo=UTC), schedule)
    c._compute_cheap_window()
    assert c._data.cheapest_window_start == dt.datetime(2026, 5, 2, 2, tzinfo=UTC)
    assert c._data.cheapest_window_end == dt.datetime(2026, 5, 2, 6, tzinfo=UTC)
    assert c._data.cheapest_window_avg_ore == 50


def test_excludes_hours_past_deadline(fixed_now):
    """The deadline (next 08:00 CPH = 06:00 UTC) caps the search range."""
    c = _coordinator()
    # Hours 07:00..11:00 UTC are past the 06:00 UTC deadline; even if cheap they
    # must be excluded, leaving the pre-deadline cluster as the only choice.
    schedule = [
        500, 500, 500, 500, 500, 500, 500, 500, 500, 500, 500, 500, 500,
        100, 100, 100, 100,
        1, 1, 1, 1, 1,
    ]
    c._data.prices = _prices_for_24h(dt.datetime(2026, 5, 1, 13, tzinfo=UTC), schedule)
    c._compute_cheap_window()
    assert c._data.cheapest_window_start == dt.datetime(2026, 5, 2, 2, tzinfo=UTC)
    assert c._data.cheapest_window_end == dt.datetime(2026, 5, 2, 6, tzinfo=UTC)


def test_hysteresis_keeps_window_before_it_starts(fixed_now):
    """Once a window is set and its end is in the future, recompute is a no-op
    even if a cheaper window has appeared. This is the core regression fix:
    the start sensor must not slide forward as `now` advances, because each
    new value re-arms the time trigger and produces a spurious charge fire."""
    c = _coordinator()
    # Pre-set a window that's still upcoming.
    c._data.cheapest_window_start = dt.datetime(2026, 5, 2, 2, tzinfo=UTC)
    c._data.cheapest_window_end = dt.datetime(2026, 5, 2, 6, tzinfo=UTC)
    c._data.cheapest_window_avg_ore = 80

    # New prices that, absent hysteresis, would prefer 13:00..16:00 UTC at 1 øre.
    schedule = [
        1, 1, 1, 1, 1, 1, 1, 1,
        500, 500, 500, 500, 500, 500, 500, 500, 500,
    ]
    c._data.prices = _prices_for_24h(dt.datetime(2026, 5, 1, 13, tzinfo=UTC), schedule)
    c._compute_cheap_window()

    assert c._data.cheapest_window_start == dt.datetime(2026, 5, 2, 2, tzinfo=UTC)
    assert c._data.cheapest_window_end == dt.datetime(2026, 5, 2, 6, tzinfo=UTC)
    assert c._data.cheapest_window_avg_ore == 80


def test_hysteresis_keeps_window_during_charge(fixed_now):
    """Mid-charge: prev_start has already passed but prev_end is still ahead.
    The Apr 30 14:00 → May 1 05:50 incident was caused by recomputing here and
    overwriting the 18:00 stop time with next-day's window."""
    fixed_now.value = dt.datetime(2026, 4, 30, 15, 30, tzinfo=UTC)  # mid-window
    c = _coordinator()
    c._data.cheapest_window_start = dt.datetime(2026, 4, 30, 14, tzinfo=UTC)
    c._data.cheapest_window_end = dt.datetime(2026, 4, 30, 18, tzinfo=UTC)
    c._data.cheapest_window_avg_ore = 60

    schedule = [50] * 24
    c._data.prices = _prices_for_24h(dt.datetime(2026, 4, 30, 16, tzinfo=UTC), schedule)
    c._compute_cheap_window()

    # Window stays put — the in-flight stop trigger lives.
    assert c._data.cheapest_window_start == dt.datetime(2026, 4, 30, 14, tzinfo=UTC)
    assert c._data.cheapest_window_end == dt.datetime(2026, 4, 30, 18, tzinfo=UTC)


def test_recomputes_after_window_ends(fixed_now):
    """Once prev_end is in the past, the next cycle is computed normally."""
    fixed_now.value = dt.datetime(2026, 5, 1, 12, tzinfo=UTC)
    c = _coordinator()
    # A previous window that has already ended.
    c._data.cheapest_window_start = dt.datetime(2026, 5, 1, 2, tzinfo=UTC)
    c._data.cheapest_window_end = dt.datetime(2026, 5, 1, 6, tzinfo=UTC)
    c._data.cheapest_window_avg_ore = 70

    schedule = [
        200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200,
        100, 100, 30, 30, 30, 30,
    ]
    c._data.prices = _prices_for_24h(dt.datetime(2026, 5, 1, 13, tzinfo=UTC), schedule)
    c._compute_cheap_window()

    assert c._data.cheapest_window_start == dt.datetime(2026, 5, 2, 2, tzinfo=UTC)
    assert c._data.cheapest_window_end == dt.datetime(2026, 5, 2, 6, tzinfo=UTC)
    assert c._data.cheapest_window_avg_ore == 30


def test_recomputes_when_prev_end_equals_now(fixed_now):
    """Boundary: prev_end == now is treated as 'past' (we use strict `<`)."""
    fixed_now.value = dt.datetime(2026, 5, 1, 6, tzinfo=UTC)
    c = _coordinator()
    c._data.cheapest_window_start = dt.datetime(2026, 5, 1, 2, tzinfo=UTC)
    c._data.cheapest_window_end = dt.datetime(2026, 5, 1, 6, tzinfo=UTC)

    # No prices for the next deadline cycle yet — should clear the stale window.
    c._data.prices = []
    c._compute_cheap_window()
    assert c._data.cheapest_window_start is None
    assert c._data.cheapest_window_end is None


def test_no_slide_simulating_apr30_regression(fixed_now):
    """Replays the production sequence: at every price refresh through the
    afternoon, the previously-set window must remain at the originally-chosen
    start/end. This is what stops the hourly false-start cascade."""
    c = _coordinator()
    # Anchor: at 11:00 UTC the cheapest window 11:00..15:00 UTC was chosen.
    fixed_now.value = dt.datetime(2026, 4, 30, 10, 42, tzinfo=UTC)
    schedule = [
        # 11:00..14:00 UTC: the cheapest cluster
        30, 30, 30, 30,
        # 15:00..23:00 UTC: pricey
        200, 200, 200, 200, 200, 200, 200, 200, 200,
        # 00:00..07:00 UTC May 1: pricey too (we want the daytime window to win)
        180, 180, 180, 180, 180, 180, 180, 180,
    ]
    c._data.prices = _prices_for_24h(dt.datetime(2026, 4, 30, 11, tzinfo=UTC), schedule)
    c._compute_cheap_window()
    chosen_start = c._data.cheapest_window_start
    chosen_end = c._data.cheapest_window_end
    assert chosen_start == dt.datetime(2026, 4, 30, 11, tzinfo=UTC)
    assert chosen_end == dt.datetime(2026, 4, 30, 15, tzinfo=UTC)

    # Simulate hourly price refreshes at 11:01, 12:02, 13:02, 14:03 UTC.
    # Pre-fix, each of these slid the start sensor forward by one hour.
    for ts in [
        dt.datetime(2026, 4, 30, 11, 1, tzinfo=UTC),
        dt.datetime(2026, 4, 30, 12, 2, tzinfo=UTC),
        dt.datetime(2026, 4, 30, 13, 2, tzinfo=UTC),
        dt.datetime(2026, 4, 30, 14, 3, tzinfo=UTC),
    ]:
        fixed_now.value = ts
        c._compute_cheap_window()
        assert c._data.cheapest_window_start == chosen_start, f"slid at {ts}"
        assert c._data.cheapest_window_end == chosen_end, f"end moved at {ts}"
