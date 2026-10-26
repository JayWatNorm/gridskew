# Forecast archive ingestion

How `raw.carbon_intensity_forecast` is loaded. For what the data means, see
[010_forecast.md](010_forecast.md). For the reasoning behind these patterns, see
[../ingestion-patterns.md](../ingestion-patterns.md).

**Status: running in production since 2026-08-17.**

| | |
|---|---|
| Module | `ingestion/carbon_intensity/forecast_poller.py` |
| Table | `raw.carbon_intensity_forecast` |
| DDL | `sql/migrations/V001__carbon_intensity_forecast.sql` |
| DAG | `dags/gridskew_carbon_intensity_forecast_dag.py`, `dag_id` `gridskew_carbon_intensity` |
| Schedule | `5,35 * * * *` (five minutes after each half-hour boundary); `0,30` until 10 September 2026 |
| `catchup` | **`False`** |

The five-minute schedule offset reduces the risk of polling while NESO is still
publishing its boundary update. The API response has no publication timestamp
or revision identifier, so a successful request at exactly `:00` or `:30`
cannot prove that it received the new forecast vintage.

## `catchup=False` is load-bearing here

**This source is not addressable in time.** The API re-runs its model every 30
minutes and overwrites the stored value in place, so a request for a past period
returns the final revision, not the forecast that existed then.

A backfilled run would therefore fetch **current** data for every historical
logical interval. The poller uses the actual execution time for `retrieved_at`,
so those runs would store copies of the current forecast, not the missing
historical revisions.

This is the opposite of the addressable Elexon time series such as PN, which use
`catchup=True`. The setting follows from the source, not from preference.

## There is no backfill, and there never can be

**The past is unrecoverable.** The archive extends forwards only, from the first
scheduled poll on 2026-08-17.

That is why this was the first thing built in the whole project: every day it did
not exist is a day of forecast evolution that cannot be reconstructed. The Elexon
datasets could wait, because their history is still retrievable.

## Volume

The API normally returns 96 rows per poll — **roughly 1.7M rows and 250 MB a
year.** Polls made on the half-hour, as they were until 10 September 2026,
returned 97: the window also held the half-hour that had just ended
([Carbon forecast trajectory](../../carbon-forecast-trajectory.md#which-polls-count)).

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
required and non-null; `actual` must be present but may be null.

Warning-only rows remain loadable. Rejected rows are quarantined with the
request timestamp and 48-hour horizon, compatible rows are loaded, and the task
then fails so the rejection remains visible.

After loading compatible rows, the poller requires unique, consecutive
30-minute periods whose first interval contains the request time. An
internally broken window remains stored for diagnosis, and the task fails
visibly.

A window that ends less than 48 hours after its first period starts is
different. The source feed sometimes stops extending and repeats its last
forecast for hours or days; every such poll measured to date equals the poll
before it. The poller stores the short window once, logs a warning and
succeeds, because a retry would store the same rows again. A poll with fewer
than 96 rows is therefore a repeat of a frozen feed, not a new forecast, and
models must not count it as one.

## The question this table answers

Does the forecast drift one way between its first and its final version? The
rule for reading the answer is fixed before any drift value is read, and the
answer is read once. The rule, and how short and repeated polls are treated,
are in [Carbon forecast trajectory](../../carbon-forecast-trajectory.md).

## Politeness

`User-Agent: gridskew/0.1 (+https://github.com/JayWatNorm/gridskew)`.

The terms state a rate limit exists but do not publish it, and prohibit
concealing an application's identity. Two requests an hour is not a concern.
