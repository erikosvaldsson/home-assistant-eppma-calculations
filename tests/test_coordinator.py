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


async def test_night_multiplier_of_zero_excludes_night_hours(
    enable_custom_integrations, hass: HomeAssistant
) -> None:
    """Multiplier of 0 means night hours contribute 0 to the average — any
    candidate peak that falls inside the night window is effectively ignored."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_SOURCE_ENERGY_SENSOR: "sensor.fake_energy",
            CONF_PEAKS_PER_MONTH: 3,
            CONF_NIGHT_START_HOUR: 22,
            CONF_NIGHT_END_HOUR: 6,
            CONF_NIGHT_MULTIPLIER: 0.0,
        },
        options={},
        entry_id="zero-mult",
    )
    entry.add_to_hass(hass)
    coord = EppmaCoordinator(hass, entry)

    night = datetime(2026, 4, 14, 2, 0, tzinfo=timezone.utc)
    day = datetime(2026, 4, 14, 14, 0, tzinfo=timezone.utc)
    assert coord._adjust(4.0, night) == 0.0
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

    with patch.object(coord, "_fetch_hourly", side_effect=fake_fetch), patch.object(
        coord, "_fetch_last_closed_hour", return_value=None
    ):
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

    with patch.object(coord, "_fetch_hourly", side_effect=fake_fetch), patch.object(
        coord, "_fetch_last_closed_hour", return_value=None
    ):
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

    with patch.object(coord, "_fetch_hourly", side_effect=fake_fetch), patch.object(
        coord, "_fetch_last_closed_hour", return_value=None
    ):
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

    with patch.object(coord, "_fetch_hourly", side_effect=fake_fetch), patch.object(
        coord, "_fetch_last_closed_hour", return_value=None
    ):
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

    with patch.object(coord, "_fetch_hourly", side_effect=fake_empty), patch.object(
        coord, "_fetch_last_closed_hour", return_value=None
    ):
        data = await coord._async_update_data()

    assert data.current_month_peaks == []
    assert data.current_month_average == 0.0
    assert data.current_month_lowest_peak == 0.0
    assert data.last_month_peaks == []
    assert data.last_month_average == 0.0
    assert data.last_month_lowest_peak == 0.0
    assert data.last_hour is None


async def test_last_hour_reads_state_history_at_hour_boundaries(
    enable_custom_integrations, hass: HomeAssistant, freezer
) -> None:
    """`last_hour` is derived from the source sensor's state at the two
    hour boundaries (not from the long-term statistics bucket), so it
    reflects the hour that just closed as soon as the refresh fires."""
    coord = await _build_coordinator(hass)

    local_tz = dt_util.DEFAULT_TIME_ZONE
    frozen = datetime(2026, 4, 15, 14, 0, 0, tzinfo=local_tz)
    freezer.move_to(frozen)

    # Only the 12:00-13:00 statistics bucket is present; the 13:00-14:00
    # bucket has not been compiled yet at 14:00:00.
    async def fake_fetch_hourly(start, end):
        from custom_components.eppma_calculations.coordinator import HourlyPeak
        start_local = dt_util.as_local(start)
        if start_local.month == 4 and start_local.year == 2026:
            return [
                HourlyPeak(
                    start=datetime(2026, 4, 15, 12, 0, tzinfo=local_tz),
                    raw_kwh=1.0,
                    adjusted_kwh=1.0,
                )
            ]
        return []

    async def fake_fetch_boundary(hour_start_utc):
        local = dt_util.as_local(hour_start_utc)
        if local.hour == 13 and local.day == 15:
            return 100.0
        if local.hour == 14 and local.day == 15:
            return 105.0
        return None

    with patch.object(coord, "_fetch_hourly", side_effect=fake_fetch_hourly), patch.object(
        coord, "_fetch_hour_start_state", side_effect=fake_fetch_boundary
    ):
        data = await coord._async_update_data()

    assert data.last_hour is not None
    assert data.last_hour.start == datetime(2026, 4, 15, 13, 0, tzinfo=local_tz)
    assert data.last_hour.raw_kwh == pytest.approx(5.0)
    assert data.last_hour.adjusted_kwh == pytest.approx(5.0)  # 13:00 is daytime


async def test_last_hour_applies_night_multiplier(
    enable_custom_integrations, hass: HomeAssistant, freezer
) -> None:
    coord = await _build_coordinator(hass)
    local_tz = dt_util.DEFAULT_TIME_ZONE
    freezer.move_to(datetime(2026, 4, 15, 3, 0, 0, tzinfo=local_tz))

    async def fake_fetch_boundary(hour_start_utc):
        local = dt_util.as_local(hour_start_utc)
        if local.hour == 2:
            return 200.0
        if local.hour == 3:
            return 208.0
        return None

    with patch.object(
        coord, "_fetch_hourly", return_value=[]
    ), patch.object(
        coord, "_fetch_hour_start_state", side_effect=fake_fetch_boundary
    ):
        data = await coord._async_update_data()

    assert data.last_hour is not None
    assert data.last_hour.raw_kwh == pytest.approx(8.0)
    assert data.last_hour.adjusted_kwh == pytest.approx(4.0)  # 02:00 is night


async def test_last_hour_is_none_when_state_history_missing(
    enable_custom_integrations, hass: HomeAssistant, freezer
) -> None:
    coord = await _build_coordinator(hass)
    local_tz = dt_util.DEFAULT_TIME_ZONE
    freezer.move_to(datetime(2026, 4, 15, 14, 0, 0, tzinfo=local_tz))

    async def no_state(hour_start_utc):
        return None

    with patch.object(
        coord, "_fetch_hourly", return_value=[]
    ), patch.object(
        coord, "_fetch_hour_start_state", side_effect=no_state
    ):
        data = await coord._async_update_data()

    assert data.last_hour is None


async def test_last_hour_clips_counter_reset_to_zero(
    enable_custom_integrations, hass: HomeAssistant, freezer
) -> None:
    """If the end-boundary state is lower than the start-boundary state
    (source counter reset), raw consumption is clamped to 0."""
    coord = await _build_coordinator(hass)
    local_tz = dt_util.DEFAULT_TIME_ZONE
    freezer.move_to(datetime(2026, 4, 15, 14, 0, 0, tzinfo=local_tz))

    async def reset_boundary(hour_start_utc):
        local = dt_util.as_local(hour_start_utc)
        if local.hour == 13:
            return 9999.0
        if local.hour == 14:
            return 3.0
        return None

    with patch.object(
        coord, "_fetch_hourly", return_value=[]
    ), patch.object(
        coord, "_fetch_hour_start_state", side_effect=reset_boundary
    ):
        data = await coord._async_update_data()

    assert data.last_hour is not None
    assert data.last_hour.raw_kwh == 0.0
    assert data.last_hour.adjusted_kwh == 0.0
