"""Button platform tests."""
from __future__ import annotations

from unittest.mock import AsyncMock

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.eppma_calculations.button import EppmaRefreshButton
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


async def test_button_press_triggers_refresh(
    enable_custom_integrations, hass: HomeAssistant
) -> None:
    entry = _make_entry()
    entry.add_to_hass(hass)
    coord = EppmaCoordinator(hass, entry)
    coord.async_request_refresh = AsyncMock()
    button = EppmaRefreshButton(coord, entry)

    await button.async_press()

    coord.async_request_refresh.assert_awaited_once()
    assert button.unique_id == "test-entry_refresh"
