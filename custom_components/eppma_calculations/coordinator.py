"""Data update coordinator for EPPMA Calculations."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging

from homeassistant.components.recorder import get_instance, history
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

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

_LOGGER = logging.getLogger(__name__)


@dataclass
class HourlyPeak:
    """A single hour's adjusted energy consumption."""

    start: datetime
    raw_kwh: float
    adjusted_kwh: float

    def as_attribute(self) -> dict:
        return {
            "energy_kwh": round(self.raw_kwh, 4),
            "adjusted_energy_kwh": round(self.adjusted_kwh, 4),
            "time_epoch": int(self.start.timestamp()),
            "time_iso": self.start.isoformat(),
        }


@dataclass
class EppmaData:
    """Computed EPPMA state for one refresh cycle."""

    current_month_peaks: list[HourlyPeak] = field(default_factory=list)
    last_month_peaks: list[HourlyPeak] = field(default_factory=list)
    current_month_average: float | None = None
    last_month_average: float | None = None
    current_month_lowest_peak: float | None = None
    last_month_lowest_peak: float | None = None
    last_hour: HourlyPeak | None = None


class EppmaCoordinator(DataUpdateCoordinator[EppmaData]):
    """Coordinates EPPMA calculations derived from recorder statistics.

    Refreshes on the hour so the value for the hour that just closed is
    picked up from the recorder's long-term statistics.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        data = {**entry.data, **entry.options}
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.entry_id}",
            update_interval=None,
        )
        self.entry = entry
        self.source_entity: str = data[CONF_SOURCE_ENERGY_SENSOR]
        self.peaks_per_month: int = int(
            data.get(CONF_PEAKS_PER_MONTH, DEFAULT_PEAKS_PER_MONTH)
        )
        self.night_start: int = int(
            data.get(CONF_NIGHT_START_HOUR, DEFAULT_NIGHT_START_HOUR)
        )
        self.night_end: int = int(
            data.get(CONF_NIGHT_END_HOUR, DEFAULT_NIGHT_END_HOUR)
        )
        self.night_multiplier: float = float(
            data.get(CONF_NIGHT_MULTIPLIER, DEFAULT_NIGHT_MULTIPLIER)
        )

    def async_register_hourly_refresh(self) -> None:
        """Hook up the top-of-hour refresh. Called once during entry setup."""
        unsub = async_track_time_change(
            self.hass, self._async_hourly_tick, minute=0, second=0
        )
        self.entry.async_on_unload(unsub)

    async def _async_hourly_tick(self, _now: datetime) -> None:
        await self.async_request_refresh()

    def _is_night(self, hour_local: int) -> bool:
        if self.night_start == self.night_end:
            return False
        if self.night_start < self.night_end:
            return self.night_start <= hour_local < self.night_end
        return hour_local >= self.night_start or hour_local < self.night_end

    def _adjust(self, kwh: float, start_local: datetime) -> float:
        return kwh * self.night_multiplier if self._is_night(start_local.hour) else kwh

    async def _fetch_hour_start_state(
        self, hour_start_utc: datetime
    ) -> float | None:
        """Return the source sensor's reading at the top of the current hour.

        Queries the recorder's state history for the last value recorded at
        or before `hour_start_utc`. This is available within seconds of the
        source sensor changing state and so avoids the small write lag of
        long-term statistics.
        """
        recorder = get_instance(self.hass)

        def _query() -> list:
            changes = history.state_changes_during_period(
                self.hass,
                hour_start_utc,
                hour_start_utc + timedelta(seconds=1),
                self.source_entity,
                no_attributes=True,
                include_start_time_state=True,
            )
            return changes.get(self.source_entity, [])

        states = await recorder.async_add_executor_job(_query)
        if not states:
            return None
        try:
            return float(states[0].state)
        except (TypeError, ValueError, AttributeError):
            return None

    async def _fetch_last_closed_hour(self) -> HourlyPeak | None:
        """Return consumption for the hour that just closed.

        Read directly from state history so the value is available as soon
        as HA records a state past the hour boundary.
        """
        now_local = dt_util.now()
        current_hour_local = now_local.replace(
            minute=0, second=0, microsecond=0
        )
        last_hour_local = current_hour_local - timedelta(hours=1)

        start_val = await self._fetch_hour_start_state(
            dt_util.as_utc(last_hour_local)
        )
        end_val = await self._fetch_hour_start_state(
            dt_util.as_utc(current_hour_local)
        )
        if start_val is None or end_val is None:
            return None

        raw = end_val - start_val
        if raw < 0:
            raw = 0.0
        return HourlyPeak(
            start=last_hour_local,
            raw_kwh=raw,
            adjusted_kwh=self._adjust(raw, last_hour_local),
        )

    async def _fetch_hourly(
        self, start: datetime, end: datetime
    ) -> list[HourlyPeak]:
        """Fetch hourly energy consumption from recorder statistics."""
        recorder = get_instance(self.hass)
        stats = await recorder.async_add_executor_job(
            statistics_during_period,
            self.hass,
            start,
            end,
            {self.source_entity},
            "hour",
            None,
            {"change"},
        )
        rows = stats.get(self.source_entity, [])
        peaks: list[HourlyPeak] = []
        for row in rows:
            change = row.get("change")
            if change is None:
                continue
            start_utc = (
                dt_util.utc_from_timestamp(row["start"])
                if isinstance(row["start"], (int, float))
                else row["start"]
            )
            start_local = dt_util.as_local(start_utc)
            raw = float(change)
            if raw < 0:
                raw = 0.0
            peaks.append(
                HourlyPeak(
                    start=start_local,
                    raw_kwh=raw,
                    adjusted_kwh=self._adjust(raw, start_local),
                )
            )
        return peaks

    @staticmethod
    def _top_peaks_distinct_days(
        peaks: list[HourlyPeak], n: int
    ) -> list[HourlyPeak]:
        """Pick the top-N peaks across N distinct days (EPPMA rule)."""
        by_day: dict[object, HourlyPeak] = {}
        for p in peaks:
            key = p.start.date()
            cur = by_day.get(key)
            if cur is None or p.adjusted_kwh > cur.adjusted_kwh:
                by_day[key] = p
        daily_max = sorted(
            by_day.values(), key=lambda x: x.adjusted_kwh, reverse=True
        )
        return daily_max[:n]

    def _month_bounds(self, ref: datetime) -> tuple[datetime, datetime]:
        start = ref.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if start.month == 12:
            end = start.replace(year=start.year + 1, month=1)
        else:
            end = start.replace(month=start.month + 1)
        return start, end

    async def _async_update_data(self) -> EppmaData:
        try:
            now_local = dt_util.now()
            cur_start, _ = self._month_bounds(now_local)
            prev_ref = cur_start - timedelta(days=1)
            prev_start, prev_end = self._month_bounds(prev_ref)

            cur_hourly = await self._fetch_hourly(
                dt_util.as_utc(cur_start), dt_util.as_utc(now_local)
            )
            prev_hourly = await self._fetch_hourly(
                dt_util.as_utc(prev_start), dt_util.as_utc(prev_end)
            )

            cur_peaks = self._top_peaks_distinct_days(cur_hourly, self.peaks_per_month)
            prev_peaks = self._top_peaks_distinct_days(prev_hourly, self.peaks_per_month)

            cur_avg = (
                sum(p.adjusted_kwh for p in cur_peaks) / len(cur_peaks)
                if cur_peaks
                else 0.0
            )
            prev_avg = (
                sum(p.adjusted_kwh for p in prev_peaks) / len(prev_peaks)
                if prev_peaks
                else 0.0
            )
            cur_low = min((p.adjusted_kwh for p in cur_peaks), default=0.0)
            prev_low = min((p.adjusted_kwh for p in prev_peaks), default=0.0)

            last_hour = await self._fetch_last_closed_hour()

            return EppmaData(
                current_month_peaks=cur_peaks,
                last_month_peaks=prev_peaks,
                current_month_average=cur_avg,
                last_month_average=prev_avg,
                current_month_lowest_peak=cur_low,
                last_month_lowest_peak=prev_low,
                last_hour=last_hour,
            )
        except Exception as err:  # noqa: BLE001
            raise UpdateFailed(f"EPPMA update failed: {err}") from err
