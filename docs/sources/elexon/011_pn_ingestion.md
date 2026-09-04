# PN ingestion

How `raw.elexon_pn` is loaded. For what the data means and what the columns are,
see [010_pn.md](010_pn.md). For the reasoning behind these patterns, see
[../ingestion-patterns.md](../ingestion-patterns.md).

**Status: deployed, backfill complete 2026-08-23.** 366 runs covering
2025-08-22 to 2026-08-23, 46,190,258 rows, no failures.

The deployed job predates the local endpoint-validation change described
below. The validation and quarantine path is tested in the development branch
but is not deployed.

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

Verified against a full day on dev, 2026-08-22: **132,728 rows**. The full year
since loaded averages **126,203 rows per run** — see Volume below.

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

## Validation and quarantine

The local PN poller validates every fetched row before typed parsing. Rows with
no validation errors continue, including warning-only rows. Rejected findings
are excluded from `raw.elexon_pn` and passed to `raw.endpoint_quarantine` with
their request window, retrieval time, zero-based source index, observed fields,
validation errors and complete source payload.

The routing and writer boundary are covered without a live API or database.
This is not yet a production control: warning evidence, fail-after-commit
behaviour for mixed/all-rejected runs and malformed response-container handling
remain outstanding. See [../endpoint-validation.md](../endpoint-validation.md).

## `load` does not catch exceptions, deliberately

A failed insert must raise, so Airflow marks the task failed and the three
configured retries fire. Catching and logging would leave the task **green with
an empty table**.

This matters most for constraint violations. A `NotNullViolation` raised on the
first load is information — it says the table's assumptions and the source's
behaviour disagree, and it says so immediately. Swallowed, the same violation
surfaces weeks later as missing data with no obvious cause.

Row-count assertions and logging remain outstanding. `raise_for_status()`
catches HTTP failures, but the current path does not distinguish a successful
empty or unexpectedly small response from an ordinary run.

## Window cap: tested and closed, 2026-08-21

Market-wide requests, no `bmUnit` filter:

| Window | Rows returned | Rows per day | Ratio to 1 day |
|---|---|---|---|
| 1 day | 133,388 | 133,388 | 1.00 |
| 7 days | 897,839 | 128,263 | 6.73 |
| 18 days | 2,299,340 | 127,741 | 17.24 |

**Linear, with no truncation observed through 18 days.** The 18-day request
returned roughly 626 MB in a single response. Client memory is therefore the
binding constraint at the tested depths; the existence of a higher undocumented
cap remains unknown.

## Volume, counted

**Counted across the completed backfill, 2026-08-23** — a full census of
`raw.elexon_pn`, not a sample:

| | |
|---|---|
| Rows | **46,190,258** |
| Runs | 366 |
| Rows per run | **126,203** average |
| Rows per settlement period | ~2,600 |
| Distinct units | **2,552** |
| On disk, table and index | **7,780 MB** |
| Density | **177 bytes per row** |

Composition of those rows:

| | Rows | Share |
|---|---|---|
| Zero to zero | 32,492,793 | **70.3%** |
| Ramps, `level_from <> level_to` | 3,525,386 | **7.6%** |
| Negative levels | 5,508,814 | **11.9%** |
| Null `bm_unit` | 657,743 | **1.4%** |

Three of those matter downstream. The **7.6% ramp share** is what the settlement
period integration macro exists for — naive endpoint integration is wrong on
roughly one row in thirteen, which is common enough to be a correctness problem
rather than an edge case. The **11.9% negative share** rules out any check
constraint assuming generation is positive. The **70.3% zero share** is why
filtering happens at the registry join downstream rather than at ingest.

**Single-day samples run about 5% high.** The 132,728 measured on 2026-08-22 sits
above the 126,203 yearly mean, so any figure extrapolated from one day inherits
that day's weather and market conditions. Volumes stated as annual totals in this
repository should be counted, not multiplied.

> **Count, do not infer.** A response's byte size is not a row count — measuring
> tools truncate, and a truncated response looks like a small one. Every volume
> on this page comes from `count(*)`, which is also why
> [../ingestion-patterns.md](../ingestion-patterns.md) asserts on row counts
> rather than status codes.

## Backfill verification

Two queries confirm a backfill landed intact. Neither can use an index — the
primary key leads on `national_grid_bm_unit` — so both are full scans, and
questions should be batched into as few passes as possible.

The second is the useful one, because **its answer is predictable from a
calendar before it runs**:

```sql
SELECT settlement_date, count(DISTINCT settlement_period) AS periods
FROM raw.elexon_pn
GROUP BY settlement_date
HAVING count(DISTINCT settlement_period) <> 48
ORDER BY settlement_date;
```

Result for the 2025-08-22 backfill:

| Date | Periods | Why |
|---|---|---|
| 2025-08-22 | 47 | first run, partial — see below |
| 2025-10-26 | **50** | clocks go back, 25-hour local day |
| 2026-03-29 | **46** | clocks go forward, 23-hour local day |
| 2026-08-23 | 3 | most recent run, still in progress |

Anything else appearing in that list is a failed run or a gap.

**The 50 is the load-bearing one.** On the October clock change, local 01:30
occurs twice. Because `time_from` is built from the API's UTC string, those
become 00:30Z and 01:30Z — distinct key values. Had the key used a naive local
timestamp, `ON CONFLICT DO NOTHING` would have discarded the second hour
silently and the day would have loaded as 48 periods.

**The first day is a partial by design.** A UTC window opens an hour after the
BST settlement day starts, so period 1 falls before the earliest run and no
earlier run exists to collect it. Period 2 survives only because the API includes
the period containing the requested `from`. The documented history therefore
starts with this expected partial day.

## Chunking

**One day per request.** 366 requests for a year, ~34 MB and ~126,000 rows each,
roughly 150 MB once materialised in Python.

Larger windows work at the API but not on a shared Airflow worker: 7 days is
~236 MB of JSON and near a gigabyte in memory; 30 days over three gigabytes.

Settings that follow:

- `timeout=30`, not 10. A daily chunk is fifty times larger than anything the
  carbon intensity pollers fetch.
- `page_size=1000` on `execute_values`. The default of 100 means 1,300 round
  trips per daily chunk.

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

**A one-slot Airflow Pool serialises all Elexon work.** `max_active_runs` limits
one DAG but does not prevent another Elexon DAG from running at the same time.
Every Elexon task uses the `elexon` pool, so only one of the four DAGs can
materialise a response on the shared worker at once. See
[../../deployment.md](../../deployment.md).

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
extra rows and 1.3 GB, one-off. Running `1, 8, 30, 90` permanently would cost 4×
storage — about 32.6 GB a year for PN and 61.9 GB with QPN — to monitor
something with no evidence it happens. A rolling 90-day PN window would cost
about 734 GB a year for the same reach.

## Result

*To be recorded here when the experiment concludes.*
