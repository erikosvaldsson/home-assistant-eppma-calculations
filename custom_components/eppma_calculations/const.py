"""Constants for the EPPMA Calculations integration."""
from __future__ import annotations

DOMAIN = "eppma_calculations"

CONF_SOURCE_ENERGY_SENSOR = "source_energy_sensor"
CONF_PEAKS_PER_MONTH = "peaks_per_month"
CONF_NIGHT_START_HOUR = "night_start_hour"
CONF_NIGHT_END_HOUR = "night_end_hour"
CONF_NIGHT_MULTIPLIER = "night_multiplier"

DEFAULT_PEAKS_PER_MONTH = 3
DEFAULT_NIGHT_START_HOUR = 22
DEFAULT_NIGHT_END_HOUR = 6
DEFAULT_NIGHT_MULTIPLIER = 0.5
