# Outturn ingestion

How `raw.carbon_intensity_outturn` is loaded. For what the data means, see
[020_outturn.md](020_outturn.md). For the reasoning behind these patterns, see
[../ingestion-patterns.md](../ingestion-patterns.md).

**Status: running in production since 2026-08-19. Runtime validation,
quarantine and period-completeness checks are deployed.**

| | |
|---|---|
| Module | `ingestion/carbon_intensity/outturn_poller.py` |
| Table | `raw.carbon_intensity_outturn` |
| DDL | `sql/init/002_carbon_intensity_outturn.sql` |
| DAG | `dags/gridskew_carbon_intensity_outturn_dag.py` |
| Schedule | `0 6 * * *` |
| `catchup` | **`False`**, with a rolling look-back |

## The one poller that does two jobs at once

This source **is** addressable in time, unlike the forecast — a request for last
March returns last March. So `catchup=True` would work for loading history.

It is off because the poller has a second job that `catchup` cannot do:
**re-polling recent periods to pick up late actuals.** A `catchup=True` run
fetches its own interval and never returns, so an `actual` landing three days
after the period would never be captured.

Instead, `run` takes its window from the database. `stored_period_summary`
reads the existing minimum and maximum `period_start`; an empty or short table
triggers a **365 day backfill in 30 day windows**, otherwise a **7 day
look-back**. Backfill and catch-up use the same path with different dates.

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

## Why it is acceptable here and would be ruinous on PN

The 7 day look-back re-fetches every period seven times.

| Source | Rows/day | Cost of a 7-day look-back |
|---|---|---|
| **This table** | 48 | **336 rows/day — free** |
| Elexon PN | 126,203 | 883,421 rows/day — **~57 GB/year** |

**At forty-eight rows a day there is no reason to separate the two jobs.** At a
hundred and twenty-six thousand, the same design would consume most of a
terabyte within a few years, which is why the Elexon pollers split "load history"
from "check for revisions" and this one does not.

> The PN figures are measured across the completed backfill: 126,203 rows per
> run at 177 bytes per row. See
> [../elexon/011_pn_ingestion.md](../elexon/011_pn_ingestion.md).

> The DAGs use different patterns because the source volumes and revision
> behaviour differ. This poller combines the historical and recent-data paths
> because the repeated volume is negligible.

## Window cap: 30 days, partially tested

`from`/`to` appears to cap at **30 days**, which is what `build_windows` uses. A
year is 13 requests.

**31 days has not been tested**, so whether the API errors or truncates silently
beyond 30 is unconfirmed. Silent truncation is the dangerous case — it returns
200 and looks healthy. Worth establishing before the chunk size is ever raised.

Contrast with Elexon PN, where no truncation was observed in requests up to 18
days. Neither result proves an unlimited range.

## Chunk boundaries overlap by one period

Each chunk starts where the last ended, and the API returns the period containing
that instant. With `retrieved_at` constant across a run, that is a **guaranteed**
primary key violation, so the insert carries `ON CONFLICT DO NOTHING`.

This only bites when a window starts exactly on a half hour, which is precisely
when scheduled runs fire. A manual run starting mid-period will not reproduce it.

The overlap is deliberate: **a duplicate is caught by the primary key, a gap is
silent.**

## Volume

48 rows per day of new periods, times the 7 day look-back, is about **123,000
rows a year**. Negligible.

## Politeness

`User-Agent: gridskew/0.1 (+https://github.com/JayWatNorm/gridskew)`.

A backfill is 13 requests; a daily run is one. No throttling needed.

## Findings from this table

The hour-zero asymmetry test ran against a year of it on 2026-08-19. Median of
`actual - forecast_final` was -1 gCO2/kWh over 17,522 periods, with actual above
forecast 47.5% of the time excluding ties, deduplicated with `DISTINCT ON`.
