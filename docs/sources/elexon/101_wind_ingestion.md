# Wind forecast and outturn ingestion

How `raw.elexon_windfor` and `raw.elexon_fuelhh` are loaded. For what the
data means, see [100_wind.md](100_wind.md). For the reasoning behind these
patterns, see [../ingestion-patterns.md](../ingestion-patterns.md).

**One-off back-fill, no DAG.** It holds a year of history for the Q30
analysis (`dbt/analyses/q30_wind_vs_carbon_error.sql`): WINDFOR publish days
20 August 2025 to 16 August 2026 and FUELHH WIND settlement dates 22 August
2025 to 16 August 2026.

| | |
|---|---|
| Module | `ingestion/elexon/wind_poller.py` |
| Back-fill | `ingestion/elexon/wind_backfill.py` |
| Tables | `raw.elexon_windfor`, `raw.elexon_fuelhh` |
| DDL | `sql/migrations/V012__elexon_wind.sql` |
| Tests | `tests/test_elexon_wind.py` |
| dbt | `stg_elexon__windfor`, `stg_elexon__fuelhh`, `int_wind_error_by_period` |

## What is captured

Elexon keeps every WINDFOR issue, so a past year can be read as it was
forecast at the time. A request for publish day 22 August 2025 returned
eight issues of 73 hourly points each, 584 rows. FUELHH, asked for one
settlement date and fuel type WIND, returned one row per settlement period.

`raw.elexon_windfor` keeps every issue: `publish_time` is part of its key.
`raw.elexon_fuelhh` keeps every publication: `publish_time` is part of its
key too, so a later value for a period lands beside the first. In both,
`retrieved_at` records when a row was first stored, and fetching a window or
a date again inserts nothing.

## One request at a time

`run_windfor(conn, publish_from, publish_to)` asks `/datasets/WINDFOR` for
one publish-time window, with `publishDateTimeFrom` and `publishDateTimeTo`
in UTC. `run_fuelhh(conn, settlement_date)` asks `/datasets/FUELHH` for one
settlement date, with `settlementDateFrom` and `settlementDateTo` both that
date and `fuelType=WIND`. Neither endpoint documents a maximum range, so
each request covers one day.

The response must be a dictionary holding a `data` list; an empty list loads
nothing. A row the contract rejects, such as a point with a null
`generation`, is quarantined, and then no row of the request is loaded, so a
day is stored whole or not at all. The run then fails that day with
`ContractRejected`.

## The back-fill

The back-fill walks WINDFOR's UTC publish days from two days before the
first settlement date read (so the last issue before its first half-hour,
which starts at 23:00 UTC on 21 August 2025, is held) to the last settlement
date, then FUELHH's settlement dates. One request at a time, a second apart,
WINDFOR first:

| Dataset | Unit per request | Requests | Rows, about |
|---|---|---|---|
| WINDFOR | one UTC publish day | 362 | 211,000 |
| FUELHH WIND | one settlement date | 360 | 17,280 |
| **Total** | | **722** | **228,000** |

At a second apart the run takes about 15 to 25 minutes and transfers about
27 MB. Each request prints a summary line.

**A WINDFOR publish day is held when it stores at least 8 distinct publish
times.** In the summer and winter samples all eight issues fell inside the
UTC day. **A FUELHH settlement date is held when it stores exactly its
calendar's number of distinct periods**: 46, 48 or 50. A held day or date is
skipped, so running the back-fill again fetches only those still missing or
short, including one a stopped run left part-way. Whether one request
returns all 50 periods of an autumn clock-change date is untested; if it
does not, the date stays not held and is listed.

A failed request is reported and the run goes on; the exit code is 1 when
any failed. The final report lists, for each dataset, the days still not
held after the run: a failed day, or a day the source served short. A short
day is not a failure; the coverage check before the analysis is read
records it.

Run it in the Airflow container, which holds the `gridskew_prod` connection,
after V012 is applied:

```bash
python -m ingestion.elexon.wind_backfill --dry-run
python -m ingestion.elexon.wind_backfill
```

The dry run makes no request: it prints, for each dataset, its first and
last day, the days held, the days it would fetch, and the total requests to
make (722 on an empty table).

## Freshness

None. The tables grow only when the back-fill runs, so the dbt sources carry
no freshness check.
