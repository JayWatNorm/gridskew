# Outturn ingestion

How `raw.carbon_intensity_outturn` is loaded. For what the data means, see
[020_outturn.md](020_outturn.md). For the reasoning behind these patterns, see
[../ingestion-patterns.md](../ingestion-patterns.md).

**Status: running in production since 2026-08-19.**

| | |
|---|---|
| Module | `ingestion/carbon_intensity/outturn_poller.py` |
| Table | `raw.carbon_intensity_outturn` |
| DDL | `sql/migrations/V002__carbon_intensity_outturn.sql` |
| DAG | `dags/gridskew_carbon_intensity_outturn_dag.py` |
| Schedule | `0 6 * * *` |
| `catchup` | **`False`**, with a rolling look-back |

## The one poller that does two jobs at once

This source **is** addressable in time, unlike the forecast — a request for last
March returns last March. So `catchup=True` would work for loading history.

It is off because the poller has a second job that `catchup` cannot do:
**re-polling recent periods to pick up revisions.** A `catchup=True` run
fetches its own interval and never returns, so a value revised after that run
would never be captured.

Instead, `run` takes its window from the database. `stored_period_summary`
reads the stored row count and minimum/maximum `period_start`. An empty table,
or an earliest stored period later than the one-year horizon, triggers a
**365 day backfill in 30 day windows**; otherwise the poller requests a
**7 day look-back**. The row count and latest period are logged only; gaps are
found by the Monday check below.

## Re-fetching gaps in history

An interrupted initial load can commit an early window and leave a gap in the
middle, and a DAG that stays paused for more than a week leaves one behind the
look-back. The seven-day look-back reaches neither.

The Monday run therefore compares the stored history with the expected
half-hours, from one day inside the one-year horizon to the start of the
look-back, and re-fetches every 30-day window that holds a missing period. It
logs a warning naming each window and its missing count.

- **The look-back runs first.** A failing history request cannot stop late
  actuals from landing.
- **History is re-fetched on Mondays only.** The limit is the day of the week,
  not an attempt count, so a period the source never supplies is requested once
  a week, not every day. A gap waits up to six days for its first attempt.
- **A history request that fails does not stop the others.** The failure is
  logged and the remaining windows are still attempted. A database fault is
  not a failed request: it fails the run.
- **Airflow retries on a Monday repeat the history requests**, 12 per attempt
  at most.
- **A re-fetched window that is still incomplete is a logged warning**, after
  its rows are stored. A period the source never supplies stays missing in
  every re-fetch, so failing on it would turn every Monday run red for a year.
  An incomplete recent window, and any rejected row, still fails the run; a
  rejected row fails it at once, without a retry, because a retry fetches the
  same rows.

## Validation and quarantine

Each request must return a dictionary containing a non-empty `data` list. Rows
are checked against the outturn contract, which requires non-null `actual` and
final forecast values.

Rejected rows retain that chunk's `from` and `to` context in quarantine.
Compatible rows are still loaded. During a backfill, validation-rejected chunks
do not block later chunks; the run raises once after all requested windows. See
[Chunk boundaries overlap by one period](#chunk-boundaries-overlap-by-one-period)
for the target-key behavior.

Each chunk must also contain unique, consecutive 30-minute periods covering the
API's observed inclusive request boundaries. Bounds inside a period align to
the following half-hour boundary; an exact boundary remains unchanged. A
seven-day request therefore derives 337 periods rather than relying on a
hard-coded count. Incomplete chunks remain stored for diagnosis, do not block
later chunks and cause the run to fail after all requested windows have been
attempted.

## Why a 7-day look-back suits this source and not PN

The 7 day look-back re-fetches every period seven times.

| Source | Rows/day | Cost of a 7-day look-back |
|---|---|---|
| **This table** | 48 | **336 rows/day — free** |
| Elexon PN | 126,203 | 883,421 rows/day — **~57 GB/year** |

**At forty-eight rows a day there is no reason to separate the two jobs.** At a
hundred and twenty-six thousand, the same design would add about 57 GB a year,
which is why the Elexon pollers split "load history" from "check for revisions"
and this one does not.

> The PN figures are measured across the completed backfill: 126,203 rows per
> run at 177 bytes per row. See
> [../elexon/011_pn_ingestion.md](../elexon/011_pn_ingestion.md).

## Window cap: 30 days

`build_windows` requests at most 30 days per call, so a year is 13 requests. A
response that ends before its requested window fails the period-completeness
check. See [020_outturn.md](020_outturn.md#endpoint).

## Chunk boundaries overlap by one period

Each chunk starts where the last ended, and the API returns the period containing
that instant. With `retrieved_at` constant across a run, that is a **guaranteed**
primary key violation, so the insert carries `ON CONFLICT DO NOTHING`.

The same applies where adjacent backfill windows meet and where a Monday
history window overlaps the look-back; the loader keeps one row per target key.

The overlap is deliberate: **a duplicate is caught by the primary key, a gap is
silent.**

## Volume

48 rows per day of new periods, times the 7 day look-back, is about **123,000
rows a year**. Negligible.

## Politeness

`User-Agent: gridskew/0.1 (+https://github.com/JayWatNorm/gridskew)`.

A backfill is 13 requests; a daily run is one. A Monday run adds one for each
history window with a gap (12 at most). No throttling needed.

## Findings from this table

See [020_outturn.md](020_outturn.md#findings-from-this-table).
