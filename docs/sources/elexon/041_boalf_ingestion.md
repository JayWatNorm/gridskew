# BOALF ingestion

How `raw.elexon_boalf` is loaded. For what the data means, see
[040_boalf.md](040_boalf.md). For the reasoning behind these patterns, see
[../ingestion-patterns.md](../ingestion-patterns.md).

**Status: built; not yet released.**

| | |
|---|---|
| Module | `ingestion/elexon/boalf_poller.py` |
| Table | `raw.elexon_boalf` |
| DDL | `sql/migrations/V008__elexon_boalf.sql` |
| Tests | `tests/test_elexon_boalf.py`, against `boalf_stream.json` |
| DAG | `dags/gridskew_elexon_boalf_dag.py` |
| Schedule | daily at 00:00 UTC, with a full-day data interval |
| `start_date` | 2025-08-22, the first PN day |
| `catchup` | **`True`** |

## The same shape as PN

`run(conn, from_date=None, to_date=None)` loads one UTC window; the DAG passes
its data interval. One day per request, `timeout=30`, `page_size=1000`, the
one-slot `elexon` pool, and the validation and quarantine routing are as in
[011_pn_ingestion.md](011_pn_ingestion.md). A day is about 27,000 rows and
10 MB, a fifth of a PN day.

The source is addressable by time, so `catchup=True` loads the history: one
run per day from the first PN day, so every day that has a notification also
has its acceptances. The API served 2025-08-22 when asked on 2026-10-07.

## Every poll is kept

BOALF rows carry no revision number, so `retrieved_at` is part of the key, as
in PN. A repeated request lands as new rows.

The request includes both ends of its window. Each daily run therefore also
stores the ramp points that start at the midnight ending its day, and the
next run stores them again. `int_elexon__boa_by_period` reads the latest
capture of each ramp point, so the repeat is counted once.

## The schedule is a timetable, not `@daily`

`CronDataIntervalTimetable("0 0 * * *", timezone="UTC")` gives each run the
day before it as its data interval on both Airflow 2 and Airflow 3. The task
refuses a run that has no data interval: to repeat a day, clear its scheduled
task instance.

## An empty response fails the run

A day without a single acceptance has not been observed, so an empty list is
treated as a failed request, as in PN.
