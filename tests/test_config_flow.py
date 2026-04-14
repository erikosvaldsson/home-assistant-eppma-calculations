"""Tests for the EPPMA Calculations config flow."""
from __future__ import annotations

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.eppma_calculations.const import (
    CONF_NIGHT_END_HOUR,
    CONF_NIGHT_MULTIPLIER,
    CONF_NIGHT_START_HOUR,
    CONF_PEAKS_PER_MONTH,
    CONF_SOURCE_ENERGY_SENSOR,
    DOMAIN,
)


async def test_user_flow_creates_entry(
    recorder_mock, enable_custom_integrations, hass: HomeAssistant
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_SOURCE_ENERGY_SENSOR: "sensor.energy_meter",
            CONF_PEAKS_PER_MONTH: 3,
            CONF_NIGHT_START_HOUR: 22,
            CONF_NIGHT_END_HOUR: 6,
            CONF_NIGHT_MULTIPLIER: 0.5,
        },
    )
    assert result2["type"] == FlowResultType.CREATE_ENTRY
    assert result2["data"][CONF_SOURCE_ENERGY_SENSOR] == "sensor.energy_meter"
    assert result2["data"][CONF_PEAKS_PER_MONTH] == 3
    assert result2["data"][CONF_NIGHT_MULTIPLIER] == 0.5


async def test_user_flow_rejects_duplicate_source(
    recorder_mock, enable_custom_integrations, hass: HomeAssistant
) -> None:
    payload = {
        CONF_SOURCE_ENERGY_SENSOR: "sensor.energy_meter",
        CONF_PEAKS_PER_MONTH: 3,
        CONF_NIGHT_START_HOUR: 22,
        CONF_NIGHT_END_HOUR: 6,
        CONF_NIGHT_MULTIPLIER: 0.5,
    }
    first = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    await hass.config_entries.flow.async_configure(first["flow_id"], payload)

    second = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        second["flow_id"], payload
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"
