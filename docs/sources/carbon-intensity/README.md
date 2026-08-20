# NESO Carbon Intensity API

The official carbon intensity API for Great Britain, published by the
**National Energy System Operator (NESO)** and developed with Environmental
Defense Fund Europe, the University of Oxford Department of Computer Science,
and WWF.

| | |
|---|---|
| Base URL | `https://api.carbonintensity.org.uk` |
| Auth | None |
| Licence | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) |
| Docs | https://carbon-intensity.github.io/api-definitions/ |
| Terms | https://github.com/carbon-intensity/terms |

Carbon intensity is measured in **gCO2/kWh** at half-hourly settlement period
granularity, for the GB electricity system as a whole.

## Datasets ingested

| Page | Endpoint | Table |
|---|---|---|
| [010_forecast.md](010_forecast.md) | `/intensity/{from}/fw48h` | `raw.carbon_intensity_forecast` |
| [020_outturn.md](020_outturn.md) | `/intensity/{from}/{to}` | `raw.carbon_intensity_outturn` |

## Shared behaviour

**Timestamp format is `YYYY-MM-DDTHH:MMZ`, with no seconds.** Parse and build
with `"%Y-%m-%dT%H:%MZ"`. This differs from the Elexon API, which uses seconds
and is inconsistent about the timezone suffix.

**All times are UTC.** The UK observes BST from late March to late October, so
never compare an API timestamp to a local clock.

**A request returns the period containing the requested `from` time.** Ask from
`14:30Z` and the first row returned is the period ending at `14:30`. Chunked
requests therefore overlap by one period at every join, which is deliberate:
an overlap produces a duplicate the primary key can catch, whereas a gap is
silent.

**Rate limit exists but is not published.** The terms state that NESO applies
one and may block applications making a large number of calls. Set a
descriptive `User-Agent`, since the terms also prohibit concealing an
application's identity:

```
gridskew/0.1 (+https://github.com/JayWatNorm/gridskew)
```

**`intensity.index` is not stable over time.** The bands (`very low`, `low`,
`moderate`, `high`, `very high`) get redefined, so the field is stored but
nullable and should not be relied on across long periods.

## The behaviour this whole project exists because of

The API **re-runs its forecast model every 30 minutes and overwrites the stored
value in place**. Asking today what it predicted for yesterday afternoon
returns the final revision, made minutes before the event, not the 48-hour-ahead
prediction.

There is therefore no public record of how a forecast changed as its period
approached. `raw.carbon_intensity_forecast` exists to build that record, and it
only extends forwards from the day it started.
