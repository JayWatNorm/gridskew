# Outturn ingestion

How `raw.carbon_intensity_outturn` is loaded. For what the data means, see
[020_outturn.md](020_outturn.md). For the reasoning behind these patterns, see
[../ingestion-patterns.md](../ingestion-patterns.md).

**Status: deployed and running in production since 2026-08-19.**

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

Instead, `run` takes its window from the database. `data_checker` reads the
existing min and max `period_start`; an empty or short table triggers a **365 day
backfill in 30 day chunks**, otherwise a **7 day look-back**. Backfill and
catch-up are the same code path with different dates, which is what stops them
drifting apart.

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

> The PN figures above were **estimates until 2026-08-23** and are now counted:
> 126,203 rows per run against an assumed 130,000, and **177 bytes per row
> against an assumed 154**. The density error made the look-back look 15% cheaper
> than it is. Working in [../elexon/011_pn_ingestion.md](../elexon/011_pn_ingestion.md).

> If asked why the DAGs are inconsistent: they are not picking different patterns
> for the same problem. This one merges two jobs because the volume makes the
> merge free.

## Window cap: 30 days, partially tested

`from`/`to` appears to cap at **30 days**, which is what the chunker uses. A year
is 13 requests.

**31 days has not been tested**, so whether the API errors or truncates silently
beyond 30 is unconfirmed. Silent truncation is the dangerous case — it returns
200 and looks healthy. Worth establishing before the chunk size is ever raised.

Contrast with Elexon, where the equivalent question *was* tested and the answer
was no cap at all.

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
forecast 47.5% of the time excluding ties, deduplicated with `DISTINCT ON`. The
pre-registered demotion rule was followed rather than rewritten.
