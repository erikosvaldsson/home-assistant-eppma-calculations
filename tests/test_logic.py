"""Pure-logic tests — no running HA instance needed."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from custom_components.eppma_calculations.coordinator import (
    EppmaCoordinator,
    HourlyPeak,
)


def _peak(iso: str, kwh: float, adjusted: float | None = None) -> HourlyPeak:
    start = datetime.fromisoformat(iso)
    return HourlyPeak(
        start=start, raw_kwh=kwh, adjusted_kwh=kwh if adjusted is None else adjusted
    )


def test_top_peaks_picks_one_per_day_and_sorts_descending() -> None:
    peaks = [
        _peak("2026-04-01T10:00:00+00:00", 2.0),
        _peak("2026-04-01T18:00:00+00:00", 5.0),
        _peak("2026-04-02T08:00:00+00:00", 3.0),
        _peak("2026-04-03T09:00:00+00:00", 4.0),
        _peak("2026-04-04T12:00:00+00:00", 1.0),
    ]
    top = EppmaCoordinator._top_peaks_distinct_days(peaks, 3)
    assert [p.adjusted_kwh for p in top] == [5.0, 4.0, 3.0]
    assert [p.start.date().isoformat() for p in top] == [
        "2026-04-01",
        "2026-04-03",
        "2026-04-02",
    ]


def test_top_peaks_handles_fewer_days_than_n() -> None:
    peaks = [_peak("2026-04-01T10:00:00+00:00", 2.0)]
    top = EppmaCoordinator._top_peaks_distinct_days(peaks, 3)
    assert len(top) == 1
    assert top[0].adjusted_kwh == 2.0


def test_top_peaks_empty_input() -> None:
    assert EppmaCoordinator._top_peaks_distinct_days([], 3) == []


def test_multiple_spikes_same_day_collapse_to_one() -> None:
    """Effekttariff rule: a high peak is overshadowed by a higher one on
    the same calendar day — only the day's maximum contributes.
    """
    peaks = [
        # Three very high spikes all on April 1. Only the highest counts.
        _peak("2026-04-01T09:00:00+00:00", 7.0),
        _peak("2026-04-01T12:00:00+00:00", 9.5),
        _peak("2026-04-01T18:00:00+00:00", 8.0),
        # Smaller but on distinct days — these should join the result.
        _peak("2026-04-02T12:00:00+00:00", 3.0),
        _peak("2026-04-03T12:00:00+00:00", 2.5),
    ]
    top = EppmaCoordinator._top_peaks_distinct_days(peaks, 3)
    assert [p.adjusted_kwh for p in top] == [9.5, 3.0, 2.5]
    # April 1 must only appear once.
    assert sum(1 for p in top if p.start.date().isoformat() == "2026-04-01") == 1


def test_all_hours_on_single_day_yields_single_peak() -> None:
    """If every hour of the month is on one day (pathological), the distinct-
    day rule still collapses them to a single peak — not three clones."""
    peaks = [
        _peak(f"2026-04-01T{h:02d}:00:00+00:00", float(h))
        for h in range(8, 20)
    ]
    top = EppmaCoordinator._top_peaks_distinct_days(peaks, 3)
    assert len(top) == 1
    assert top[0].adjusted_kwh == 19.0  # the 19:00 sample


def test_night_multiplier_can_demote_a_raw_peak_below_a_daytime_one() -> None:
    """Effekttariff quirk: off-peak hours are weighted less, so a raw
    nighttime spike can be ranked lower than a smaller daytime peak."""
    peaks = [
        # Nighttime: raw=8 but adjusted at x0.5 = 4
        HourlyPeak(
            start=datetime.fromisoformat("2026-04-01T03:00:00+00:00"),
            raw_kwh=8.0,
            adjusted_kwh=4.0,
        ),
        # Daytime: raw=5, adjusted=5 — beats the night spike after adjustment
        HourlyPeak(
            start=datetime.fromisoformat("2026-04-02T14:00:00+00:00"),
            raw_kwh=5.0,
            adjusted_kwh=5.0,
        ),
    ]
    top = EppmaCoordinator._top_peaks_distinct_days(peaks, 2)
    assert top[0].start.date().isoformat() == "2026-04-02"
    assert top[1].start.date().isoformat() == "2026-04-01"


def test_highest_within_day_beats_earlier_lower_value() -> None:
    """If two hours fall on the same day, the later-but-higher one wins —
    not simply the first one seen."""
    peaks = [
        _peak("2026-04-01T09:00:00+00:00", 3.0),
        _peak("2026-04-01T19:00:00+00:00", 7.0),  # later & higher
    ]
    top = EppmaCoordinator._top_peaks_distinct_days(peaks, 3)
    assert len(top) == 1
    assert top[0].start.hour == 19
    assert top[0].adjusted_kwh == 7.0


def test_hourly_peak_as_attribute_shape() -> None:
    ts = "2026-04-01T10:00:00+00:00"
    p = HourlyPeak(start=datetime.fromisoformat(ts), raw_kwh=2.1234567, adjusted_kwh=1.0617283)
    attrs = p.as_attribute()
    assert set(attrs) == {"amplitude_kwh", "raw_amplitude_kwh", "time_epoch", "time_iso"}
    assert attrs["amplitude_kwh"] == 1.0617
    assert attrs["raw_amplitude_kwh"] == 2.1235
    assert attrs["time_iso"] == ts
    assert attrs["time_epoch"] == int(datetime.fromisoformat(ts).timestamp())
