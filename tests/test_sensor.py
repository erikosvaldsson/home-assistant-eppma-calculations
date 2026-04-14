"""Sensor platform tests — focus on live EppmaThisHourSensor behaviour."""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant, State
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
from custom_components.eppma_calculations.sensor import EppmaThisHourSensor

SOURCE = "sensor.fake_energy"


def _make_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_SOURCE_ENERGY_SENSOR: SOURCE,
            CONF_PEAKS_PER_MONTH: 3,
            CONF_NIGHT_START_HOUR: 22,
            CONF_NIGHT_END_HOUR: 6,
            CONF_NIGHT_MULTIPLIER: 0.5,
        },
        options={},
        entry_id="test-entry",
    )


class _SensorHandle:
    def __init__(self, sensor: EppmaThisHourSensor) -> None:
        self.sensor = sensor

    def cleanup(self) -> None:
        # Fire any async_on_remove callbacks (listener unregisters) so no
        # timers/state-change subscriptions leak past the test.
        for cb in list(getattr(self.sensor, "_on_remove", None) or []):
            cb()
        self.sensor._on_remove = None


async def _add_sensor(
    hass: HomeAssistant,
    freezer,
    initial_state: str | None,
    now: datetime,
    hour_start_value: float | None = None,
) -> _SensorHandle:
    freezer.move_to(now)
    entry = _make_entry()
    entry.add_to_hass(hass)
    coord = EppmaCoordinator(hass, entry)

    if initial_state is not None:
        hass.states.async_set(SOURCE, initial_state)

    async def fake_fetch_hour_start(hour_start_utc):
        return hour_start_value

    coord._fetch_hour_start_state = AsyncMock(side_effect=fake_fetch_hour_start)

    sensor = EppmaThisHourSensor(coord, entry)
    sensor.hass = hass
    sensor.async_write_ha_state = lambda: None  # no platform registered
    await sensor.async_added_to_hass()
    return _SensorHandle(sensor)


async def test_live_accumulation_on_state_changes(
    enable_custom_integrations, hass: HomeAssistant, freezer
) -> None:
    """Each source state change advances `this_hour` without waiting for a
    coordinator refresh."""
    local_tz = dt_util.DEFAULT_TIME_ZONE
    now = datetime(2026, 4, 14, 14, 0, 30, tzinfo=local_tz)
    handle = await _add_sensor(hass, freezer, "100.0", now, hour_start_value=100.0)
    sensor = handle.sensor
    try:
        freezer.move_to(now + timedelta(minutes=5))
        hass.states.async_set(SOURCE, "100.2")
        await hass.async_block_till_done()
        assert sensor.native_value == 0.2
        assert sensor.extra_state_attributes["energy_kwh"] == 0.2

        freezer.move_to(now + timedelta(minutes=20))
        hass.states.async_set(SOURCE, "100.75")
        await hass.async_block_till_done()
        assert sensor.native_value == 0.75
    finally:
        handle.cleanup()


async def test_hour_rollover_resets_counter(
    enable_custom_integrations, hass: HomeAssistant, freezer
) -> None:
    """Crossing into a new hour re-baselines so the counter starts from zero."""
    local_tz = dt_util.DEFAULT_TIME_ZONE
    now = datetime(2026, 4, 14, 14, 30, 0, tzinfo=local_tz)
    handle = await _add_sensor(hass, freezer, "200.0", now, hour_start_value=200.0)
    sensor = handle.sensor
    try:
        freezer.move_to(now + timedelta(minutes=10))
        hass.states.async_set(SOURCE, "200.4")
        await hass.async_block_till_done()
        assert sensor.native_value == 0.4

        freezer.move_to(datetime(2026, 4, 14, 15, 0, 20, tzinfo=local_tz))
        hass.states.async_set(SOURCE, "200.45")
        await hass.async_block_till_done()
        # delta from last-known 200.4 → 200.45 = 0.05
        assert sensor.native_value == 0.05
    finally:
        handle.cleanup()


async def test_night_multiplier_applied(
    enable_custom_integrations, hass: HomeAssistant, freezer
) -> None:
    """During night hours the adjusted value is multiplied by the night factor."""
    local_tz = dt_util.DEFAULT_TIME_ZONE
    # 02:30 is inside the 22→06 night window from the fixture (multiplier 0.5)
    now = datetime(2026, 4, 14, 2, 30, 0, tzinfo=local_tz)
    handle = await _add_sensor(hass, freezer, "10.0", now, hour_start_value=10.0)
    sensor = handle.sensor
    try:
        freezer.move_to(now + timedelta(minutes=5))
        hass.states.async_set(SOURCE, "12.0")
        await hass.async_block_till_done()

        # State is raw; attributes expose both raw and night-adjusted values.
        assert sensor.native_value == 2.0
        assert sensor.extra_state_attributes["energy_kwh"] == 2.0
        assert sensor.extra_state_attributes["adjusted_energy_kwh"] == 1.0  # 2.0 * 0.5
    finally:
        handle.cleanup()


async def test_counter_reset_rebaselines(
    enable_custom_integrations, hass: HomeAssistant, freezer
) -> None:
    """If the source counter drops (meter reset), the sensor re-baselines
    instead of going negative."""
    local_tz = dt_util.DEFAULT_TIME_ZONE
    now = datetime(2026, 4, 14, 14, 10, 0, tzinfo=local_tz)
    handle = await _add_sensor(hass, freezer, "500.0", now, hour_start_value=500.0)
    sensor = handle.sensor
    try:
        freezer.move_to(now + timedelta(minutes=5))
        hass.states.async_set(SOURCE, "500.3")
        await hass.async_block_till_done()
        assert sensor.native_value == 0.3

        freezer.move_to(now + timedelta(minutes=10))
        hass.states.async_set(SOURCE, "0.0")
        await hass.async_block_till_done()
        assert sensor.native_value == 0.0

        freezer.move_to(now + timedelta(minutes=15))
        hass.states.async_set(SOURCE, "0.05")
        await hass.async_block_till_done()
        assert sensor.native_value == 0.05
    finally:
        handle.cleanup()


async def test_hydrates_from_recorder_on_startup(
    enable_custom_integrations, hass: HomeAssistant, freezer
) -> None:
    """On startup mid-hour, the baseline is taken from the previous hour's
    long-term statistic so `this_hour` reflects true consumption, not just
    what was seen since HA started."""
    local_tz = dt_util.DEFAULT_TIME_ZONE
    now = datetime(2026, 4, 14, 14, 40, 0, tzinfo=local_tz)
    # Source currently reads 50.0; at the top of 14:00 the cumulative value
    # was 49.2 → already 0.8 kWh consumed this hour.
    handle = await _add_sensor(hass, freezer, "50.0", now, hour_start_value=49.2)
    sensor = handle.sensor
    try:
        assert sensor.native_value == 0.8  # day hour → no multiplier

        freezer.move_to(now + timedelta(minutes=5))
        hass.states.async_set(SOURCE, "50.1")
        await hass.async_block_till_done()
        assert sensor.native_value == 0.9  # 50.1 - 49.2
    finally:
        handle.cleanup()
