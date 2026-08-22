# PN ingestion

How `raw.elexon_pn` is loaded. For what the data means and what the columns are,
see [010_pn.md](010_pn.md). For the reasoning behind these patterns, see
[../ingestion-patterns.md](../ingestion-patterns.md).

**Status: built and tested, not yet deployed.** Poller, tests and DAG are
written; the table exists in dev; prod and the backfill are outstanding.

| | |
|---|---|
| Module | `ingestion/elexon/pn_poller.py` |
| Table | `raw.elexon_pn` |
| DDL | `sql/init/003_elexon_pn.sql` |
| Tests | `tests/test_elexon_pn.py`, against `pn_stream.json` |
| DAG | `dags/gridskew_elexon_pn_dag.py` |
| Schedule | `@daily` |
| `start_date` | 2025-08-22 |
| `catchup` | **`True`** |

Verified against a full day on dev, 2026-08-22: **132,728 rows**, matching the
~130,000 estimate below.

## `run` takes its window as arguments

```python
run(conn, from_date=None, to_date=None)
```

The DAG passes `data_interval_start` and `data_interval_end`; a manual run gets
yesterday, midnight to midnight UTC. **`None` defaults computed in the body**,
not in the signature — a default of `datetime.now()` would freeze at import, and
a DAG file is re-parsed constantly.

That single signature is why there is no separate backfill mode: the same
function serves 365 historical runs and every future daily one.

## `load` does not catch exceptions, deliberately

A failed insert must raise, so Airflow marks the task failed and the three
configured retries fire. Catching and logging would leave the task **green with
an empty table**.

This is not hypothetical: the first real load failed on a `NotNullViolation`
that revealed `bmUnit` can be null. A `try/except` around `load` would have
swallowed it, and the design error would have surfaced weeks later as missing
data rather than immediately as a red task.

Row counts are logged either side of the load, which is the cheapest form of
"assert on row counts, not status codes" — a day returning 3,000 rows instead of
132,000 is visible in the log.

## Window cap: tested and closed, 2026-08-21

Market-wide requests, no `bmUnit` filter:

| Window | Rows returned | Rows per day | Ratio to 1 day |
|---|---|---|---|
| 1 day | 133,388 | 133,388 | 1.00 |
| 7 days | 897,839 | 128,263 | 6.73 |
| 18 days | 2,299,340 | 127,741 | 17.24 |

**Linear. No truncation, no cap.** The 18 day request returned roughly 626 MB in
a single response, so the constraint on chunk size is client memory rather than
the API.

## Volume, measured

**About 130,000 rows per day market-wide**, or roughly **2,700 rows per
settlement period**. Against a registry of 3,055 units that is about 89 percent
of all registered units filing every period — consistent with units submitting
even when their level is zero.

**47M rows and about 7 GB per year**, and the same again for QPN.

> Earlier figures in this repository of ~13,000 rows per day were wrong by a
> factor of ten. They were inferred from the byte size of a response that had
> been truncated by the measuring tool rather than by the API, and reported as
> though they were a count. This is why the politeness rules in
> [../ingestion-patterns.md](../ingestion-patterns.md) insist on asserting row
> counts rather than status codes.

## Chunking

**One day per request.** 365 requests for a year, ~34 MB and ~130,000 rows each,
roughly 150 MB once materialised in Python.

Larger windows work at the API but not on a shared Airflow worker: 7 days is
~236 MB of JSON and near a gigabyte in memory; 30 days over three gigabytes.

Settings that follow:

- `timeout=30`, not 10. A daily chunk is fifty times larger than anything the
  carbon intensity pollers fetch.
- `page_size=1000` on `execute_values`. The default of 100 means 1,300 round
  trips per daily chunk.
- `time.sleep(0.2)` between backfill requests.

## Backfill

**`catchup=True` with `start_date` one year back.** The source is addressable by
time, so a historical DAG run genuinely does the work its logical date describes
— unlike the carbon intensity forecast, where it cannot.

Each run fetches its own `data_interval`, which means:

- no `data_checker`, no chunker, no branch between backfill and steady state
- one red square in the grid view if a single day fails, retried by
  `default_args` without touching any other day
- `max_active_runs=1` serialises the 365 runs

### Pacing, on unpausing

The scheduler creates runs in batches as intervals close, but `max_active_runs=1`
means exactly one executes at a time. Each run is roughly 30 to 60 seconds,
dominated by the insert of 132,000 rows.

```
365 runs x ~45s          ~ 4.5 hours
365 requests over 4.5h   ~ 1.4 requests per minute
```

Against the **30 requests per second** measured with no throttling, that is about
1,300 times slower. No additional delay is needed.

**Pausing mid-backfill is safe.** The in-flight run finishes, queued runs stop,
and unpausing resumes where it left off — each run is independent and idempotent.

**When QPN and B1610 join, use an Airflow Pool.** `max_active_runs` limits one
DAG; three DAGs backfilling would make three concurrent requests. A pool with one
slot, shared by all three tasks, holds the whole project to one Elexon request at
a time.

### `start_date` is the history window, and it is deliberately static

`start_date=datetime(2025, 8, 22)` is **a decision about how much history to
load**, fixed at deployment. It is not maintenance-free, and the trade-off is
worth stating.

**Redeploying from scratch later re-backfills from that date.** Stand this DAG up
on a fresh Airflow in 2028 and it creates roughly 1,100 runs rather than 365.
That is ~145M rows and about a day of running — recoverable, not free, and
probably what you would want anyway.

**Do not make `start_date` dynamic to solve this.** `datetime.now() -
timedelta(days=365)` is re-evaluated every time the scheduler parses the file,
which is roughly every thirty seconds. `start_date` is the anchor Airflow uses to
compute which intervals exist, so a moving value shifts the schedule underneath
it — runs get skipped or duplicated and the DAG's history stops being coherent.
It is the same rule as mutable default arguments: **the expression runs when the
module executes, and a DAG module executes constantly.**

**If the window ever needs changing, change it in the file.** One line, visible
in the diff, and a reader can see exactly what history the project loads.

**The alternative, if redeployment safety ever matters more than simplicity:**
set `catchup=False` with a recent `start_date`, and load history once by hand:

```bash
airflow dags backfill -s 2025-08-22 -e 2026-08-22 gridskew_elexon_pn
```

That makes the historical load an explicit operation rather than a consequence of
the DAG existing, so redeploying never triggers one. The cost is that the
backfill lives in a runbook instead of the DAG, and the "one mechanism serves
both history and the daily run" property is lost.

**Not adopted**, on the grounds that the redeployment scenario is hypothetical
and the simpler design is easier to explain.

## Only fetch periods past Gate Closure

PN is visible about eighty minutes ahead — see [010_pn.md](010_pn.md). Anything
fetched for a period that has not passed Gate Closure is provisional.

With a daily schedule and a one-day interval this takes care of itself: the run
for a given day executes after that day has ended.

## Pre-registered test: do PNs restate?

**Stated before the data arrives, so the answer cannot be chosen
retrospectively.**

Gate Closure means a PN cannot be *submitted* after its period begins. That is a
rule about submissions, not a guarantee about the endpoint — corrections,
republication and late-loaded submissions are not ruled out, and PN carries no
revision marker to make one visible.

`retrieved_at` is the only instrument. Because it is in the primary key, every
re-poll of a window stores a fresh copy.

**The experiment**: run a lag ladder of `1, 8, 30, 90` days for four weeks, then:

```sql
SELECT (retrieved_at - time_from) AS lag, count(*)
FROM (
    SELECT bm_unit, time_from, retrieved_at,
           count(DISTINCT level_from) OVER (PARTITION BY bm_unit, time_from) AS variants
    FROM raw.elexon_pn
) t
WHERE variants > 1
GROUP BY 1 ORDER BY 1;
```

**Decision rule, fixed in advance:**

- **Empty** — PNs do not restate. Drop to a single daily fetch and record the
  result here with the date.
- **Rows returned** — restatement is real. Keep the rungs where changes actually
  appear, drop the rest, and record which.

**The ladder is the experiment, not the design.** Four weeks costs about 7.3M
extra rows and 1.1 GB, one-off. Running `1, 8, 30, 90` permanently would cost 4×
storage — 29 GB a year for PN, 58 GB with QPN — to monitor something with no
evidence it happens. A rolling 90-day window would cost 655 GB a year for the
same reach.

## Result

*To be recorded here when the experiment concludes.*
