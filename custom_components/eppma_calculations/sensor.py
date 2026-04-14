"""Sensor platform for EPPMA Calculations."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import logging
from typing import Any, Callable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy
from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_change,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import EppmaCoordinator, EppmaData, HourlyPeak

_LOGGER = logging.getLogger(__name__)


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
    entities: list[Entity] = [EppmaSensor(coordinator, entry, d) for d in SENSORS]
    entities.append(EppmaThisHourSensor(coordinator, entry))
    async_add_entities(entities)


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


def _as_float(state: State | None) -> float | None:
    if state is None or state.state in ("unknown", "unavailable", None, ""):
        return None
    try:
        return float(state.state)
    except (TypeError, ValueError):
        return None


class EppmaThisHourSensor(SensorEntity):
    """Live-updating sensor for energy consumed so far in the current hour.

    Tracks the source energy sensor directly so the value ticks as the meter
    reports new readings, rather than waiting for the hourly coordinator
    refresh.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_translation_key = "this_hour"
    _attr_name = "This hour"
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 3

    def __init__(
        self, coordinator: EppmaCoordinator, entry: ConfigEntry
    ) -> None:
        self._coordinator = coordinator
        self._source_entity = coordinator.source_entity
        self._attr_unique_id = f"{entry.entry_id}_this_hour"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title or "EPPMA Calculations",
            manufacturer="EPPMA",
            model="Energy Peaks per Month Average",
        )
        self._hour_start_time: datetime | None = None
        self._hour_start_value: float | None = None
        self._last_source_value: float | None = None
        self._raw_kwh: float = 0.0
        self._adjusted_kwh: float = 0.0

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        now_local = dt_util.now()
        hour_start_local = now_local.replace(minute=0, second=0, microsecond=0)
        current = _as_float(self.hass.states.get(self._source_entity))

        raw_so_far = 0.0
        try:
            peak = await self._coordinator._fetch_current_hour(
                dt_util.as_utc(hour_start_local), dt_util.as_utc(now_local)
            )
            if peak is not None:
                raw_so_far = peak.raw_kwh
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Initial hydration for this_hour failed: %s", err)

        self._hour_start_time = hour_start_local
        self._last_source_value = current
        if current is not None:
            self._hour_start_value = current - raw_so_far
            self._raw_kwh = raw_so_far
            self._adjusted_kwh = self._apply_night(raw_so_far)
        else:
            self._hour_start_value = None
            self._raw_kwh = 0.0
            self._adjusted_kwh = 0.0

        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [self._source_entity], self._handle_source_change
            )
        )
        self.async_on_remove(
            async_track_time_change(
                self.hass, self._handle_hour_tick, minute=0, second=0
            )
        )

    def _apply_night(self, raw: float) -> float:
        if self._hour_start_time is None:
            return raw
        return (
            raw * self._coordinator.night_multiplier
            if self._coordinator._is_night(self._hour_start_time.hour)
            else raw
        )

    @callback
    def _handle_source_change(self, event: Event) -> None:
        new_val = _as_float(event.data.get("new_state"))
        if new_val is None:
            return

        now_local = dt_util.now()
        hour_start = now_local.replace(minute=0, second=0, microsecond=0)
        if self._hour_start_time != hour_start:
            # Hour rolled over between state changes; re-baseline using the
            # last seen source value (closest approximation to the value at
            # the hour boundary).
            baseline = (
                self._last_source_value
                if self._last_source_value is not None
                else new_val
            )
            self._hour_start_time = hour_start
            self._hour_start_value = baseline

        if self._hour_start_value is None:
            self._hour_start_value = new_val

        self._last_source_value = new_val
        raw = new_val - self._hour_start_value
        if raw < 0:
            # Source counter reset; re-baseline.
            self._hour_start_value = new_val
            raw = 0.0
        self._raw_kwh = raw
        self._adjusted_kwh = self._apply_night(raw)
        self.async_write_ha_state()

    @callback
    def _handle_hour_tick(self, now: datetime) -> None:
        now_local = dt_util.as_local(now)
        hour_start = now_local.replace(minute=0, second=0, microsecond=0)
        baseline = _as_float(self.hass.states.get(self._source_entity))
        if baseline is None:
            baseline = self._last_source_value
        self._hour_start_time = hour_start
        self._hour_start_value = baseline
        self._last_source_value = baseline
        self._raw_kwh = 0.0
        self._adjusted_kwh = 0.0
        self.async_write_ha_state()

    @property
    def native_value(self) -> float | None:
        if self._hour_start_time is None or self._hour_start_value is None:
            return None
        return round(self._adjusted_kwh, 4)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if self._hour_start_time is None:
            return {}
        return {
            "amplitude_kwh": round(self._adjusted_kwh, 4),
            "raw_amplitude_kwh": round(self._raw_kwh, 4),
            "time_iso": self._hour_start_time.isoformat(),
            "time_epoch": int(self._hour_start_time.timestamp()),
        }
