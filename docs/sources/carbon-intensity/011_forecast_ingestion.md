# Forecast archive ingestion

How `raw.carbon_intensity_forecast` is loaded. For what the data means, see
[010_forecast.md](010_forecast.md). For the reasoning behind these patterns, see
[../ingestion-patterns.md](../ingestion-patterns.md).

**Status: running in production since 2026-08-17. Runtime validation,
quarantine and period-completeness checks are deployed.**

| | |
|---|---|
| Module | `ingestion/carbon_intensity/forecast_poller.py` |
| Table | `raw.carbon_intensity_forecast` |
| DDL | `sql/init/001_carbon_intensity_forecast.sql` |
| DAG | `dags/gridskew_carbon_intensity_forecast_dag.py`, `dag_id` `gridskew_carbon_intensity` |
| Schedule | `5,35 * * * *` (five minutes after each half-hour boundary) |
| `catchup` | **`False`** |
| Task SLA | **35 minutes**: the 30-minute interval plus five minutes to complete |

This release adds the SLA to both the project DAG and its homelab deployment
copy. Airflow records a scheduled task that misses this deadline under
**Browse → SLA Misses**; it does not cancel, fail or retry an otherwise
successful task. Manual runs do not exercise this check. The homelab deployment
explicitly enables `core.check_slas`.

The five-minute schedule offset reduces the risk of polling while NESO is still
publishing its boundary update. The API response has no publication timestamp
or revision identifier, so a successful request at exactly `:00` or `:30`
cannot prove that it received the new forecast vintage. With the offset, a run
due at `10:05` has an SLA deadline of `10:10`.

## `catchup=False` is load-bearing here

**This source is not addressable in time.** The API re-runs its model every 30
minutes and overwrites the stored value in place, so a request for a past period
returns the final revision, not the forecast that existed then.

A backfilled run would therefore fetch **current** data for every historical
logical interval. The poller uses the actual execution time for `retrieved_at`,
so those runs would create hundreds of near-identical current observations, not
the missing historical revisions.

> Setting `catchup=True` here would not backfill history. It would write today's
> forecast hundreds of times with slightly different current timestamps and
> pollute the archive with scheduler-created duplicates of one forecast state.

This is the opposite of the Elexon datasets, which are fully addressable and use
`catchup=True`. The setting follows from the source, not from preference.

## There is no backfill, and there never can be

**The past is unrecoverable.** The archive extends forwards only, from the first
scheduled poll on 2026-08-17.

That is why this was the first thing built in the whole project: every day it did
not exist is a day of forecast evolution that cannot be reconstructed. The Elexon
datasets could wait, because their history is still retrievable.

## Volume

The API normally returns 96 or 97 rows per poll — **roughly 1.7M rows and 250
MB a year.**

At that size none of the chunking, memory or rate-limit considerations that shape
the Elexon pollers apply. Each run makes one request without splitting windows.

## `retrieved_at` must be captured once per poll

Before the HTTP request, and written identically to every row in the batch.

Generate it per row and the batch dissolves into near-identical timestamps.
The archive records *what the model believed about the next 48 hours at one
moment*; per-row timestamps would destroy that grouping while still looking
plausible.

## Validation and quarantine

The poller requires a dictionary containing a non-empty `data` list, then
validates each period against the forecast contract. `forecast` and `index` are
required; future `actual` is required but may be null.

Warning-only rows remain loadable. Rejected rows are quarantined with the
request timestamp and 48-hour horizon, compatible rows are loaded, and the task
then fails so the rejection remains visible.

After loading compatible rows, the poller requires unique, consecutive
30-minute periods whose first interval contains the request time and whose
final boundary reaches at least 48 hours after the first period starts. This
accepts valid 96- and 97-row responses while rejecting shortened or internally
broken forecast windows. An incomplete vintage remains stored for diagnosis,
but the task fails visibly.

## Politeness

`User-Agent: gridskew/0.1 (+https://github.com/JayWatNorm/gridskew)`.

The terms state a rate limit exists but do not publish it, and prohibit
concealing an application's identity. Two requests an hour is not a concern.
