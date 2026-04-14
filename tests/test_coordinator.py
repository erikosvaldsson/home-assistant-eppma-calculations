"""Coordinator tests that use the HA fixture to instantiate a real coordinator."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.eppma_calculations.const import (
    CONF_NIGHT_END_HOUR,
    CONF_NIGHT_MULTIPLIER,
    CONF_NIGHT_START_HOUR,
    CONF_PEAKS_PER_MONTH,
    CONF_SOURCE_ENERGY_SENSOR,
    DOMAIN,
)
from custom_components.eppma_calculations.coordinator import EppmaCoordinator


def _make_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_SOURCE_ENERGY_SENSOR: "sensor.fake_energy",
            CONF_PEAKS_PER_MONTH: 3,
            CONF_NIGHT_START_HOUR: 22,
            CONF_NIGHT_END_HOUR: 6,
            CONF_NIGHT_MULTIPLIER: 0.5,
        },
        options={},
        entry_id="test-entry",
    )


async def _build_coordinator(hass: HomeAssistant) -> EppmaCoordinator:
    entry = _make_entry()
    entry.add_to_hass(hass)
    return EppmaCoordinator(hass, entry)


async def test_is_night_wraps_around_midnight(
    enable_custom_integrations, hass: HomeAssistant
) -> None:
    coord = await _build_coordinator(hass)
    # night window 22 -> 6 wraps midnight
    for hour in (22, 23, 0, 3, 5):
        assert coord._is_night(hour), f"expected night at {hour}"
    for hour in (6, 10, 18, 21):
        assert not coord._is_night(hour), f"expected day at {hour}"


async def test_adjust_applies_multiplier_only_at_night(
    enable_custom_integrations, hass: HomeAssistant
) -> None:
    coord = await _build_coordinator(hass)
    night = datetime(2026, 4, 14, 2, 0, tzinfo=timezone.utc)
    day = datetime(2026, 4, 14, 14, 0, tzinfo=timezone.utc)
    assert coord._adjust(4.0, night) == 2.0
    assert coord._adjust(4.0, day) == 4.0


async def test_month_bounds_rolls_into_next_month(
    enable_custom_integrations, hass: HomeAssistant
) -> None:
    coord = await _build_coordinator(hass)
    ref = datetime(2026, 12, 17, 9, 30, tzinfo=timezone.utc)
    start, end = coord._month_bounds(ref)
    assert start == datetime(2026, 12, 1, tzinfo=timezone.utc)
    assert end == datetime(2027, 1, 1, tzinfo=timezone.utc)


def _stat_rows(base: datetime, values: list[float]) -> list[dict]:
    return [
        {"start": base + timedelta(hours=i), "change": v}
        for i, v in enumerate(values)
    ]


async def test_update_computes_peaks_and_average(
    enable_custom_integrations, hass: HomeAssistant
) -> None:
    """Feed fake recorder rows and assert EppmaData is shaped correctly."""
    coord = await _build_coordinator(hass)

    now = dt_util.now().replace(hour=12, minute=0, second=0, microsecond=0)
    month_start = now.replace(day=1)
    # Three distinct days in the current month, ascending kWh.
    cur_rows = [
        {"start": month_start.replace(day=2, hour=14), "change": 2.0},
        {"start": month_start.replace(day=3, hour=15), "change": 4.0},
        {"start": month_start.replace(day=4, hour=16), "change": 6.0},
        # second sample on a day already counted — must not bump day count
        {"start": month_start.replace(day=4, hour=10), "change": 1.0},
    ]
    prev_month_start = (month_start - timedelta(days=1)).replace(day=1)
    prev_rows = [
        {"start": prev_month_start.replace(day=5, hour=11), "change": 3.0},
        {"start": prev_month_start.replace(day=6, hour=12), "change": 5.0},
    ]

    async def fake_fetch(start, end):
        # month_start is timezone-aware; start arg is UTC
        start_local = dt_util.as_local(start)
        if start_local.month == month_start.month:
            rows = cur_rows
        else:
            rows = prev_rows
        from custom_components.eppma_calculations.coordinator import HourlyPeak
        out = []
        for row in rows:
            raw = float(row["change"])
            out.append(
                HourlyPeak(
                    start=row["start"],
                    raw_kwh=raw,
                    adjusted_kwh=coord._adjust(raw, row["start"]),
                )
            )
        return out

    with patch.object(coord, "_fetch_hourly", side_effect=fake_fetch):
        data = await coord._async_update_data()

    assert len(data.current_month_peaks) == 3
    assert sorted([p.adjusted_kwh for p in data.current_month_peaks], reverse=True) == [
        6.0,
        4.0,
        2.0,
    ]
    assert data.current_month_average == pytest.approx((6 + 4 + 2) / 3)
    assert data.current_month_lowest_peak == 2.0

    assert len(data.last_month_peaks) == 2
    assert data.last_month_average == pytest.approx((5 + 3) / 2)
    assert data.last_month_lowest_peak == 3.0


async def test_start_of_new_month_returns_none_for_this_month(
    enable_custom_integrations, hass: HomeAssistant, freezer
) -> None:
    """Edge case: at the very first hour of a new month, the recorder has
    no hourly `change` rows for the current month yet (the first hour
    hasn't closed). The 'this month' sensors must be None rather than
    raising or echoing last month's values; last-month sensors must still
    reflect the previous month's data."""
    coord = await _build_coordinator(hass)

    # Freeze time to 00:05 local on the first day of a month — before any
    # hourly statistic exists for this month.
    local_tz = dt_util.DEFAULT_TIME_ZONE
    frozen = datetime(2026, 4, 1, 0, 5, 0, tzinfo=local_tz)
    freezer.move_to(frozen)

    from custom_components.eppma_calculations.coordinator import HourlyPeak

    prev_month_rows = [
        HourlyPeak(
            start=datetime(2026, 3, 10, 15, 0, tzinfo=local_tz),
            raw_kwh=5.0,
            adjusted_kwh=5.0,
        ),
        HourlyPeak(
            start=datetime(2026, 3, 15, 16, 0, tzinfo=local_tz),
            raw_kwh=7.0,
            adjusted_kwh=7.0,
        ),
        HourlyPeak(
            start=datetime(2026, 3, 20, 17, 0, tzinfo=local_tz),
            raw_kwh=6.0,
            adjusted_kwh=6.0,
        ),
    ]

    async def fake_fetch(start, end):
        start_local = dt_util.as_local(start)
        # Current-month fetch window starts at 2026-04-01 00:00 → empty
        if start_local.month == 4 and start_local.year == 2026:
            return []
        return prev_month_rows

    with patch.object(coord, "_fetch_hourly", side_effect=fake_fetch):
        data = await coord._async_update_data()

    # "This month" has no data yet — return 0, not None
    assert data.current_month_peaks == []
    assert data.current_month_average == 0.0
    assert data.current_month_lowest_peak == 0.0

    # "Last month" still reflects March
    assert len(data.last_month_peaks) == 3
    assert data.last_month_average == pytest.approx((5 + 7 + 6) / 3)
    assert data.last_month_lowest_peak == 5.0


async def test_second_hour_of_new_month_uses_single_peak_straight_through(
    enable_custom_integrations, hass: HomeAssistant, freezer
) -> None:
    """Follow-up to the start-of-month edge case: once the first hour has
    closed, there is exactly one hourly statistic for the new month. The
    distinct-day rule collapses it to a single peak, so average == lowest
    == that one hour's adjusted value. Because 00:00 sits inside the night
    window, the multiplier has already been applied — the sensor value is
    the *adjusted* kWh, not the raw reading."""
    coord = await _build_coordinator(hass)

    local_tz = dt_util.DEFAULT_TIME_ZONE
    frozen = datetime(2026, 4, 1, 1, 5, 0, tzinfo=local_tz)
    freezer.move_to(frozen)

    from custom_components.eppma_calculations.coordinator import HourlyPeak

    raw = 8.0
    first_hour_start = datetime(2026, 4, 1, 0, 0, tzinfo=local_tz)
    cur_rows = [
        HourlyPeak(
            start=first_hour_start,
            raw_kwh=raw,
            adjusted_kwh=coord._adjust(raw, first_hour_start),
        )
    ]

    async def fake_fetch(start, end):
        start_local = dt_util.as_local(start)
        if start_local.month == 4 and start_local.year == 2026:
            return cur_rows
        return []

    with patch.object(coord, "_fetch_hourly", side_effect=fake_fetch):
        data = await coord._async_update_data()

    expected_adjusted = raw * 0.5  # night multiplier applied to 00:00
    assert len(data.current_month_peaks) == 1
    assert data.current_month_peaks[0].raw_kwh == raw
    assert data.current_month_peaks[0].adjusted_kwh == pytest.approx(expected_adjusted)
    assert data.current_month_average == pytest.approx(expected_adjusted)
    assert data.current_month_lowest_peak == pytest.approx(expected_adjusted)


async def test_last_hour_of_previous_month_does_not_leak_into_this_month(
    enable_custom_integrations, hass: HomeAssistant
) -> None:
    """The recorder fetch ranges [month_start, next_month_start). A sample
    timed at the last hour of the previous month must be returned only by
    the previous-month fetch, never by the current-month fetch."""
    coord = await _build_coordinator(hass)

    now = dt_util.now().replace(hour=12, minute=0, second=0, microsecond=0)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    prev_end = month_start - timedelta(hours=1)  # last hour of prev month

    captured: dict[str, tuple] = {}

    async def fake_fetch(start, end):
        bucket = "cur" if dt_util.as_local(start).month == month_start.month else "prev"
        captured[bucket] = (start, end)
        return []

    with patch.object(coord, "_fetch_hourly", side_effect=fake_fetch):
        await coord._async_update_data()

    cur_start, cur_end = captured["cur"]
    prev_start, prev_end_fetched = captured["prev"]

    cur_start_local = dt_util.as_local(cur_start)
    prev_end_local = dt_util.as_local(prev_end_fetched)

    # The current-month window must start at month_start and exclude prev_end.
    assert cur_start_local == month_start
    assert prev_end < cur_start_local
    # Previous-month window must end at exactly month_start (exclusive upper).
    assert prev_end_local == month_start


async def test_update_handles_empty_history(
    enable_custom_integrations, hass: HomeAssistant
) -> None:
    coord = await _build_coordinator(hass)

    async def fake_empty(start, end):
        return []

    with patch.object(coord, "_fetch_hourly", side_effect=fake_empty):
        data = await coord._async_update_data()

    assert data.current_month_peaks == []
    assert data.current_month_average == 0.0
    assert data.current_month_lowest_peak == 0.0
    assert data.last_month_peaks == []
    assert data.last_month_average == 0.0
    assert data.last_month_lowest_peak == 0.0
    assert data.last_hour is None


async def test_last_hour_is_most_recent_sample(
    enable_custom_integrations, hass: HomeAssistant
) -> None:
    """`last_hour` exposes the latest hourly sample (adjusted for night hours)."""
    coord = await _build_coordinator(hass)

    now = dt_util.now().replace(hour=12, minute=0, second=0, microsecond=0)
    month_start = now.replace(day=1)
    # Deliberately unordered; latest is day 4 @ 16:00.
    cur_rows = [
        {"start": month_start.replace(day=3, hour=15), "change": 4.0},
        {"start": month_start.replace(day=4, hour=16), "change": 6.0},
        {"start": month_start.replace(day=2, hour=14), "change": 2.0},
    ]

    async def fake_fetch(start, end):
        start_local = dt_util.as_local(start)
        if start_local.month != month_start.month:
            return []
        from custom_components.eppma_calculations.coordinator import HourlyPeak
        return [
            HourlyPeak(
                start=row["start"],
                raw_kwh=float(row["change"]),
                adjusted_kwh=coord._adjust(float(row["change"]), row["start"]),
            )
            for row in cur_rows
        ]

    with patch.object(coord, "_fetch_hourly", side_effect=fake_fetch):
        data = await coord._async_update_data()

    assert data.last_hour is not None
    assert data.last_hour.start == month_start.replace(day=4, hour=16)
    assert data.last_hour.raw_kwh == 6.0
    assert data.last_hour.adjusted_kwh == 6.0  # 16:00 is a day hour


async def test_last_hour_falls_back_to_previous_month(
    enable_custom_integrations, hass: HomeAssistant, freezer
) -> None:
    """At the very start of a new month, `last_hour` falls back to the
    latest sample from the previous month so the sensor isn't dark."""
    coord = await _build_coordinator(hass)

    local_tz = dt_util.DEFAULT_TIME_ZONE
    frozen = datetime(2026, 4, 1, 0, 5, 0, tzinfo=local_tz)
    freezer.move_to(frozen)

    from custom_components.eppma_calculations.coordinator import HourlyPeak

    latest_prev = HourlyPeak(
        start=datetime(2026, 3, 31, 23, 0, tzinfo=local_tz),
        raw_kwh=3.0,
        adjusted_kwh=1.5,  # night multiplier applied
    )
    prev_rows = [
        HourlyPeak(
            start=datetime(2026, 3, 15, 16, 0, tzinfo=local_tz),
            raw_kwh=7.0,
            adjusted_kwh=7.0,
        ),
        latest_prev,
    ]

    async def fake_fetch(start, end):
        start_local = dt_util.as_local(start)
        if start_local.month == 4 and start_local.year == 2026:
            return []
        return prev_rows

    with patch.object(coord, "_fetch_hourly", side_effect=fake_fetch):
        data = await coord._async_update_data()

    assert data.last_hour == latest_prev
