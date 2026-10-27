# Wind forecast and outturn

**WINDFOR and FUELHH (fuel type WIND): a one-off back-fill of a year of
history**, WINDFOR publish days 20 August 2025 to 16 August 2026 and FUELHH
settlement dates 22 August 2025 to 16 August 2026; see
[101_wind_ingestion.md](101_wind_ingestion.md). Other fuel types and the
actual-generation endpoints (`AGWS`, B1630) are not ingested.

## In plain terms

**How much wind power the national forecast expected, and how much the
metered wind farms produced.**

NESO's carbon intensity forecast is built from its latest forecasts of
national demand, wind and solar. When the carbon forecast misses, a wind
forecast miss is one candidate cause. This pair lets the size of the wind
miss be set beside the size of the carbon miss, half-hour by half-hour.

---

## The datasets

| | WINDFOR (forecast) | FUELHH, fuel type WIND (outturn) |
|---|---|---|
| Meaning | National wind generation forecast | Half-hourly generation outturn by fuel type |
| Scope | Wind farms visible to NESO with operational metering | Taken to be metered generation in Great Britain (inferred, not documented) |
| Granularity | Hourly points, MW | Settlement periods, average MW |
| Issues | Up to eight a day; each issue forecasts 73 hourly points, about three days | One row per period, published as the period ends |
| Endpoint | `/datasets/WINDFOR`, filtered by `publishDateTimeFrom` / `To` | `/datasets/FUELHH`, filtered by `settlementDateFrom` / `To` and `fuelType` |
| Documented maximum range | None | None |

Both base endpoints wrap rows in the `{"data": [...]}` envelope.

## Response fields

WINDFOR:

| Field | Type | Notes |
|---|---|---|
| `dataset` | `str` | `WINDFOR` |
| `publishTime` | `str` | When the issue was published, with a `Z` suffix |
| `startTime` | `str` | The hour the point forecasts |
| `generation` | `int` | MW. The specification marks it nullable |

FUELHH:

| Field | Type | Notes |
|---|---|---|
| `dataset` | `str` | `FUELHH` |
| `publishTime` | `str` | When the value was published |
| `startTime` | `str` | Period start, UTC |
| `settlementDate` | `str` | Local time date |
| `settlementPeriod` | `int` | 1 to 50 |
| `fuelType` | `str` | `WIND` here; also `CCGT`, `NUCLEAR` and others |
| `generation` | `int` | MW, averaged over the period. The specification marks it nullable |

The specification marks every FUELHH field nullable. The contract rejects a
null in any field the tables key on or store, and with it the whole request.

## Traps

**Metered wind only.** WINDFOR leaves out embedded wind (farms connected
to the distribution networks, which NESO forecasts separately), and FUELHH
appears to: on the one day compared it ran 0.4 to 0.5 GW below an estimate
that includes embedded wind. FUELHH's scope is an inference, not documented.
The carbon intensity estimate counts embedded wind, so neither side of this
pair matches the carbon model's wind. Embedded wind lowers the national
demand that NESO sees, so part of an embedded wind miss shows up as a demand
forecast miss instead.

**Issue times move with the season.** The specification lists eight issue
times with no time zone: 03:30, 05:30, 08:30, 10:30, 12:30, 16:30, 19:30 and
23:30. For 22 August 2025 the stamps were exactly those, marked UTC; for
10 December 2025 each was an hour earlier (02:30 to 22:30). The hourly
points started at 20:00 UTC in August and 21:00 UTC in December, 21:00 UK
time in both. The working reading, not documented: issues go out at fixed
UTC times, and in summer `publishTime` shows UK clock time although marked
UTC. The pairing below takes the latest issue whose source stamp is before
the half-hour's start. If the reading holds, that never uses an issue before
it existed, and in summer it may use one up to an hour older than the
newest; forecast ages are reported from the source stamp.

**A point is hourly; the outturn is half-hourly.** Whether a point is the
mean of its hour or an instant is not documented. The pairing takes the
point whose hour contains the half-hour's start, so both halves of an hour
share one point, with no interpolation.

**Whether the forecast is before or after constraints is not documented.**
No Elexon or NESO page read states whether WINDFOR forecasts output before
or after transmission constraints. NESO scores its own day-ahead unit wind
forecast only for units without a bid-offer acceptance, which suggests its
wind forecasts do not try to predict curtailment, but that is indirect
evidence. The Q30 analysis therefore uses two wind errors: outturn with the
cohort's curtailment added back, and the delivered outturn.

**Curtailment is rebuilt, not published here.** The add-back is the signed
sum of `instructed_deviation_mwh` (PN minus expected output) over cohort
units in fuel group wind with an acceptance in the half-hour, times 2 for
MW, from `int_shortfall_by_unit_period` and `int_elexon__bm_unit_cohort`.
The Q30 analysis (`dbt/analyses/q30_wind_vs_carbon_error.sql`) computes it
over its window's settlement dates, so the wind view stays outside the
period tables' lineage. It depends on PN completeness for those units; a
wind unit with an acceptance and a null instructed deviation makes the
add-back unknown.

**Zero values.** A 2012 Elexon change proposal records negative WINDFOR
values being set to zero. Whether that still applies is not known; the
coverage check counts zero forecast values.

## Pairing

`int_wind_error_by_period` holds one row per half-hour with a FUELHH wind
outturn:

- the first FUELHH WIND publication for the period;
- the latest WINDFOR issue published strictly before the half-hour starts,
  and that issue's point for the hour containing the half-hour's start. An
  issue stamped 08:30 is not used for the 08:30 half-hour. If that issue
  holds no point for the hour, the half-hour has no forecast; an older issue
  is never used in its place;
- `delivered_wind_error_mw`, outturn minus forecast. Negative means less
  wind than forecast.

The Q30 analysis joins the curtailment add-back above to each half-hour, 0
when no cohort wind unit had an acceptance, and takes the wind error as
outturn plus add-back minus forecast.

## Licence

BMRS data, used under Elexon's BMRS data licence, which requires
attribution.
