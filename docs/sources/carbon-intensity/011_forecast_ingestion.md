# Forecast archive ingestion

How `raw.carbon_intensity_forecast` is loaded. For what the data means, see
[010_forecast.md](010_forecast.md). For the reasoning behind these patterns, see
[../ingestion-patterns.md](../ingestion-patterns.md).

**Status: deployed and running in production since 2026-08-17.**

| | |
|---|---|
| Module | `ingestion/carbon_intensity/forecast_poller.py` |
| Table | `raw.carbon_intensity_forecast` |
| DDL | `sql/init/001_carbon_intensity_forecast.sql` |
| DAG | `dags/gridskew_carbon_intensity_forecast_dag.py`, `dag_id` `gridskew_carbon_intensity` |
| Schedule | `*/30 * * * *` |
| `catchup` | **`False`** |

## `catchup=False` is load-bearing here

**This source is not addressable in time.** The API re-runs its model every 30
minutes and overwrites the stored value in place, so a request for a past period
returns the final revision, not the forecast that existed then.

A backfilled run would therefore fetch **current** data and stamp it with a
`retrieved_at` implying it was observed months ago — destroying the one thing the
archive exists to record.

> Setting `catchup=True` here would not backfill history. It would write today's
> forecast 365 times with 365 different misleading timestamps.

This is the opposite of the Elexon datasets, which are fully addressable and use
`catchup=True`. The setting follows from the source, not from preference.

## There is no backfill, and there never can be

**The past is unrecoverable.** The archive extends forwards only, from the first
scheduled poll on 2026-08-17.

That is why this was the first thing built in the whole project: every day it did
not exist is a day of forecast evolution that cannot be reconstructed. The Elexon
datasets could wait, because their history is still retrievable.

## Volume

About 97 rows every 30 minutes — **roughly 1.7M rows and 250 MB a year.**

At that size none of the chunking, memory or rate-limit considerations that shape
the Elexon pollers apply. One request per run, no chunker, no ladder.

## `retrieved_at` must be captured once per poll

Before the HTTP request, and written identically to every row in the batch.

Generate it per row and the batch dissolves into 97 near-identical timestamps.
The archive records *what the model believed about the next 48 hours at one
moment*; per-row timestamps would destroy that grouping while still looking
plausible.

## Politeness

`User-Agent: gridskew/0.1 (+https://github.com/JayWatNorm/gridskew)`.

The terms state a rate limit exists but do not publish it, and prohibit
concealing an application's identity. Two requests an hour is not a concern.

## Known history

The table was truncated once, deliberately, on 2026-08-17, twelve minutes after
the first row landed. It held three deployment-artefact polls and nothing of
analytical value. **Append-only applies from the first scheduled poll after that,
and there is no second exception.**
