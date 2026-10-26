# Demand forecast and outturn ingestion

How `raw.elexon_ndf` and `raw.elexon_indo` are loaded. For what the data
means, see [050_demand.md](050_demand.md). For the reasoning behind these
patterns, see [../ingestion-patterns.md](../ingestion-patterns.md).

**One-off back-fill, no DAG.** It holds a year of history, publish days 20
August 2025 to 17 August 2026, for the Q8 and Q28 analysis
(`dbt/analyses/q8_q28_history.sql`).

| | |
|---|---|
| Module | `ingestion/elexon/demand_poller.py` |
| Back-fill | `ingestion/elexon/demand_backfill.py` |
| Tables | `raw.elexon_ndf`, `raw.elexon_indo` |
| DDL | `sql/migrations/V011__elexon_demand.sql` |
| Tests | `tests/test_elexon_demand.py`, against `ndf_truncated.json` and `indo.json` |
| dbt | `stg_elexon__ndf`, `stg_elexon__indo`, `int_demand_error_by_period` |

## What is captured

Elexon keeps every National Demand forecast (NDF) issue, so a past year can
be read as it was forecast at the time. A request for publish day 22 August
2025 returned 48 NDF issues (published at :16 and :46 past each hour), each
re-forecasting the next 35 to 82 settlement periods, all on boundary `N`.
INDO, the matching outturn, had one row per issue, published at :00 and :30.

Both tables keep every issue: `publish_time` is part of each key, and
`retrieved_at` records when a row was first stored. Fetching a window again
inserts nothing.

## One publish day per request

`run(conn, dataset, publish_from, publish_to)` asks
`/datasets/NDF` or `/datasets/INDO` for one publish-time window, with
`publishDateTimeFrom` and `publishDateTimeTo` in UTC. The base endpoint's
documented maximum range is one day. The response must be a dictionary
holding a `data` list; an empty list is a quiet window and loads nothing.
A row the contract rejects is quarantined, and then no row of the window is
loaded, so a window is stored whole or not at all.

## The back-fill

The back-fill walks UTC publish days from two days before the first
settlement date read (so the last forecast issued before its first
half-hour is held) to the day after the last one (so its first outturn is
held): 363 days for each dataset, NDF first and then INDO, one request at a
time, a second apart. Each request prints a summary line.

**A publish day is held when it stores at least 48 distinct publish
times.** Both datasets are issued every half-hour, and a UTC day always has
48 half-hours, clock change or not. A held day is skipped, so running the
back-fill again fetches only the days still missing or short, including a
day a stopped run left part-way.

A failed day is reported and the run goes on; the exit code is 1 when any day
failed. The final report lists, for each dataset, the days still not held
after the run: a failed day, or a day the source served with fewer than 48
issues. A short day is not a failure; the coverage check before the analysis
is read records it.

Run it in the Airflow container, which holds the `gridskew_prod` connection,
after V011 is applied:

```bash
python -m ingestion.elexon.demand_backfill --dry-run
python -m ingestion.elexon.demand_backfill
```

The dry run makes no request: it prints, for each dataset, the days held and
the days it would fetch.

## Freshness

None. The tables grow only when the back-fill runs, so the dbt sources carry
no freshness check.
