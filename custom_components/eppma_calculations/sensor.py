"""Sensor platform for EPPMA Calculations."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import EppmaCoordinator, EppmaData, HourlyPeak


@dataclass(frozen=True, kw_only=True)
class EppmaSensorDescription(SensorEntityDescription):
    """Description for an EPPMA sensor."""

    value_fn: Callable[[EppmaData], float | None]
    attrs_fn: Callable[[EppmaData], dict[str, Any]] | None = None


def _peaks_attrs(peaks_attr: str):
    def _fn(data: EppmaData) -> dict[str, Any]:
        peaks: list[HourlyPeak] = getattr(data, peaks_attr)
        return {"peaks": [p.as_attribute() for p in peaks]}

    return _fn


SENSORS: tuple[EppmaSensorDescription, ...] = (
    EppmaSensorDescription(
        key="this_month_average",
        translation_key="this_month_average",
        name="This month average",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
        value_fn=lambda d: d.current_month_average,
        attrs_fn=_peaks_attrs("current_month_peaks"),
    ),
    EppmaSensorDescription(
        key="this_month_lowest",
        translation_key="this_month_lowest",
        name="This month lowest",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
        value_fn=lambda d: d.current_month_lowest_peak,
    ),
    EppmaSensorDescription(
        key="last_month_average",
        translation_key="last_month_average",
        name="Last month average",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
        value_fn=lambda d: d.last_month_average,
        attrs_fn=_peaks_attrs("last_month_peaks"),
    ),
    EppmaSensorDescription(
        key="last_month_lowest",
        translation_key="last_month_lowest",
        name="Last month lowest",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
        value_fn=lambda d: d.last_month_lowest_peak,
    ),
    EppmaSensorDescription(
        key="last_hour",
        translation_key="last_hour",
        name="Last hour",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
        value_fn=lambda d: d.last_hour.adjusted_kwh if d.last_hour else None,
        attrs_fn=lambda d: d.last_hour.as_attribute() if d.last_hour else {},
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up EPPMA sensors."""
    coordinator: EppmaCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(EppmaSensor(coordinator, entry, d) for d in SENSORS)


class EppmaSensor(CoordinatorEntity[EppmaCoordinator], SensorEntity):
    """Representation of an EPPMA sensor."""

    _attr_has_entity_name = True
    entity_description: EppmaSensorDescription

    def __init__(
        self,
        coordinator: EppmaCoordinator,
        entry: ConfigEntry,
        description: EppmaSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title or "EPPMA Calculations",
            manufacturer="EPPMA",
            model="Energy Peaks per Month Average",
        )

    @property
    def native_value(self) -> float | None:
        value = self.entity_description.value_fn(self.coordinator.data)
        return round(value, 4) if value is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.coordinator.data)
