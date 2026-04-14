"""Button platform for EPPMA Calculations — manual on-demand refresh."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import EppmaCoordinator


REFRESH_BUTTON = ButtonEntityDescription(
    key="refresh",
    translation_key="refresh",
    name="Refresh",
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: EppmaCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([EppmaRefreshButton(coordinator, entry)])


class EppmaRefreshButton(ButtonEntity):
    """Recompute EPPMA values immediately."""

    _attr_has_entity_name = True
    entity_description = REFRESH_BUTTON

    def __init__(self, coordinator: EppmaCoordinator, entry: ConfigEntry) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_refresh"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title or "EPPMA Calculations",
            manufacturer="EPPMA",
            model="Energy Peaks per Month Average",
        )

    async def async_press(self) -> None:
        await self._coordinator.async_request_refresh()
