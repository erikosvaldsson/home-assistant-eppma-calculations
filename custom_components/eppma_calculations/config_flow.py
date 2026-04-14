"""Config flow for EPPMA Calculations."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_NIGHT_END_HOUR,
    CONF_NIGHT_MULTIPLIER,
    CONF_NIGHT_START_HOUR,
    CONF_PEAKS_PER_MONTH,
    CONF_SOURCE_ENERGY_SENSOR,
    DEFAULT_NIGHT_END_HOUR,
    DEFAULT_NIGHT_MULTIPLIER,
    DEFAULT_NIGHT_START_HOUR,
    DEFAULT_PEAKS_PER_MONTH,
    DOMAIN,
)


def _base_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_SOURCE_ENERGY_SENSOR,
                default=defaults.get(CONF_SOURCE_ENERGY_SENSOR),
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain="sensor", device_class="energy"
                )
            ),
            vol.Optional(
                CONF_PEAKS_PER_MONTH,
                default=defaults.get(CONF_PEAKS_PER_MONTH, DEFAULT_PEAKS_PER_MONTH),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1, max=10, step=1, mode=selector.NumberSelectorMode.BOX
                )
            ),
            vol.Optional(
                CONF_NIGHT_START_HOUR,
                default=defaults.get(
                    CONF_NIGHT_START_HOUR, DEFAULT_NIGHT_START_HOUR
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=23, step=1, mode=selector.NumberSelectorMode.BOX
                )
            ),
            vol.Optional(
                CONF_NIGHT_END_HOUR,
                default=defaults.get(CONF_NIGHT_END_HOUR, DEFAULT_NIGHT_END_HOUR),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=23, step=1, mode=selector.NumberSelectorMode.BOX
                )
            ),
            vol.Optional(
                CONF_NIGHT_MULTIPLIER,
                default=defaults.get(
                    CONF_NIGHT_MULTIPLIER, DEFAULT_NIGHT_MULTIPLIER
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0.1, max=1.0, step=0.05,
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
        }
    )


def _normalize(user_input: dict[str, Any]) -> dict[str, Any]:
    out = dict(user_input)
    for k in (CONF_PEAKS_PER_MONTH, CONF_NIGHT_START_HOUR, CONF_NIGHT_END_HOUR):
        if k in out:
            out[k] = int(out[k])
    if CONF_NIGHT_MULTIPLIER in out:
        out[CONF_NIGHT_MULTIPLIER] = float(out[CONF_NIGHT_MULTIPLIER])
    return out


class EppmaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for EPPMA Calculations."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            data = _normalize(user_input)
            await self.async_set_unique_id(data[CONF_SOURCE_ENERGY_SENSOR])
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=f"EPPMA ({data[CONF_SOURCE_ENERGY_SENSOR]})",
                data=data,
            )
        return self.async_show_form(step_id="user", data_schema=_base_schema({}))

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        return EppmaOptionsFlow(entry)


class EppmaOptionsFlow(OptionsFlow):
    """Handle options."""

    def __init__(self, entry: ConfigEntry) -> None:
        self.entry = entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=_normalize(user_input))
        defaults = {**self.entry.data, **self.entry.options}
        return self.async_show_form(step_id="init", data_schema=_base_schema(defaults))
