# Home Assistant EPPMA Calculations

A Home Assistant custom integration that computes **EPPMA** — *Energy Peaks Per
Month Average* — directly from Home Assistant's recorder long-term statistics.

Several European grid operators (e.g. Ellevio in Sweden, and similar models in
Belgium and the Netherlands) charge a **power/capacity fee** based on the
average of your highest hourly consumption peaks per month — typically the
three highest hours, each falling on a different day. Hours during off-peak
nighttime windows are often weighted differently, if at all.

This integration turns that concept into a clean set of sensors that you can
use to drive automations such as peak shaving, load shedding or smart
charging.

## Approach

The integration does **not maintain its own running state**. On every refresh
it queries the recorder's hourly `change` statistics for the configured energy
sensor, computes the peaks, and exposes the results as sensors. The recorder
is already the source of truth for energy data — EPPMA is a derived view over
it.

## Features

- Configurable source energy sensor (any `total_increasing` kWh sensor).
- Computes the top-N peaks per month across **distinct days** (EPPMA rule).
- Configurable nighttime window and multiplier (default 22:00–06:00, ×0.5).
- Previous month's peaks available for comparison.
- Recalculates every hour, on the hour, plus on Home Assistant startup.
- Refresh button for on-demand recalculation.
- Fully configurable through the UI (config flow + options flow).

## Installation

### HACS (custom repository)

1. HACS → Integrations → ⋮ → *Custom repositories*.
2. Add `https://github.com/erikosvaldsson/home-assistant-eppma-calculations`
   as an **Integration**.
3. Install *EPPMA Calculations*, then restart Home Assistant.

### Manual

Copy [custom_components/eppma_calculations](custom_components/eppma_calculations)
to your Home Assistant `config/custom_components/` directory and restart.

## Configuration

*Settings → Devices & Services → Add Integration → EPPMA Calculations.*

| Option                      | Default | Description                                                                 |
| --------------------------- | ------- | --------------------------------------------------------------------------- |
| Source energy sensor        | —       | A kWh sensor with `state_class: total_increasing` (recorder statistics).    |
| Peaks per month             | 3       | Number of distinct-day peaks to average.                                    |
| Night start / end hour      | 22 / 6  | Local-time window for night adjustment.                                     |
| Night multiplier            | 0.5     | Adjusted peak = raw peak × multiplier during night hours.                   |

All options can be changed at runtime via *Configure* on the integration card.
The integration itself refreshes automatically every hour on the hour.

## Sensors

| Sensor                                  | Description                                                                  |
| --------------------------------------- | ---------------------------------------------------------------------------- |
| `sensor.eppma_this_month_average`       | Average of the top-N peaks this month. Attributes list the peaks.            |
| `sensor.eppma_this_month_lowest`        | Smallest of the N current-month peaks.                                       |
| `sensor.eppma_last_month_average`       | Average of last month's top-N peaks. Attributes list the peaks.              |
| `sensor.eppma_last_month_lowest`        | Smallest of last month's N peaks.                                            |

All sensors are `device_class: energy` in kWh.

A `button.eppma_refresh` entity is also exposed, which triggers an immediate
recalculation without waiting for the next top-of-hour tick.

The two `*_average` sensors expose a `peaks` attribute: a list of the top-N
peaks, each with:

- `energy_kwh` — the hourly kWh as reported by the source sensor
- `adjusted_energy_kwh` — the night-multiplier-adjusted value used in the average
- `time_epoch` — start of the hour as Unix seconds
- `time_iso` — start of the hour in local-time ISO-8601

## How the calculation works

1. The integration asks the recorder for hourly `change` statistics on the
   configured energy sensor (all time zones normalized to local time).
2. Each hourly consumption value is **adjusted**: multiplied by
   `night_multiplier` if the local hour lies inside the configured night
   window.
3. Within each month, the single highest adjusted value per *day* is kept
   (distinct-day rule), then the top N are averaged.

## Development

The repository layout follows the standard HA custom-integration convention:

```
custom_components/eppma_calculations/
  __init__.py
  button.py
  config_flow.py
  const.py
  coordinator.py
  icons.json
  manifest.json
  sensor.py
  strings.json
  translations/en.json
```

Issues and merge requests are welcome at the
[GitLab project](https://gitlab.com/erik.osvaldsson/home-assistant-eppma-calculations).

## License

MIT — see [LICENSE](LICENSE).
