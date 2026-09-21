# B1610 ingestion

How `raw.elexon_b1610` is loaded. For what the data means and what the columns
are, see [020_b1610.md](020_b1610.md). For the reasoning behind these patterns,
see [../ingestion-patterns.md](../ingestion-patterns.md).

**Status: II and SF rungs deployed.** The II backfill completed 2026-08-23:
354 runs covering settlement days 2025-08-22 to 2026-08-10, with 139,316,985
rows at that checkpoint and no unexpected gaps. Scheduled II and SF capture
then continued; the exact table total was **159,151,689 rows on 2026-09-21**.

For the approved 2026-08-10 through 2026-08-16 cohort, II and SF each contain
3,083,376 rows with periods 1–48 present on every date. R1 is correctly absent
before its dated capture window.

This release adds endpoint validation to the B1610 poller. The Airflow image
includes its new Decimal-aware JSON dependency and is rolled out first, before
the server pulls the updated poller.

| | |
|---|---|
| Module | `ingestion/elexon/b1610_poller.py` |
| Table | `raw.elexon_b1610` |
| DDL | `sql/init/005_elexon_b1610.sql` |
| Tests | `tests/test_elexon_b1610.py`, against `b1610_stream.json` |
| DAGs | `gridskew_elexon_b1610_II_dag.py`, `..._SF_dag.py` |
| Schedule | `@daily`, both |
| `catchup` | II **`True`**, SF **`False`** |

## Validation and quarantine

B1610 requires a non-empty list response and validates every item before typed
parsing. It uses the same routing as PN and QPN. Its source adapter supplies a
Decimal-aware encoder so rejected quantities remain JSON numbers without
conversion to binary floats or strings. See
[../endpoint-validation.md](../endpoint-validation.md).

## Two standing rungs currently implemented

| Rung | Offset | `start_date` | Catches |
|---|---|---|---|
| **II** | `data_interval_start - 14d` | 2025-09-05 | `II` |
| **SF** | `data_interval_start - 35d` | forward only | `SF` |

`R1` through `RF` are not standing full-history rungs. They can be added without
changing the schema, but only prospectively: Elexon stops serving a run after a
later run supersedes it. Any R1, R2, R3 or RF capture already missed cannot be
recovered. Until a final-run schedule exists, the retained series is accurately
described as the first and latest captured positions, not the first and final
positions.

One bounded exception is approved. The seven settlement dates from 2026-08-10
through 2026-08-16 will be requested at fixed +56 days, on 2026-10-05 through
2026-10-11. Completion requires every response to report `R1`; this cohort will
measure II→SF and SF→R1 restatement without committing to full-history R1/R2/R3
storage.

**`start_date` is the earliest wanted settlement day plus the offset.** 2025-09-05
minus 14 days is 2025-08-22, which aligns B1610 with PN's history. A different
calculation would misalign the two tables used by the thesis join.

### Why 14 days rather than 7

II is live from day 7, but publication counts **working** days. Five working days
is seven calendar days in a normal week and about twelve around Christmas.

With `catchup=True`, a run that finds nothing writes nothing and is never
retried — so being early creates a **permanent hole**, not a delay. Seven days of
margin costs a week of freshness and removes that failure class entirely.

The 35-day rung is also a **backstop**: any day the head poll missed is picked up
there, arriving as `SF` rather than `II`. The data is not lost, only the earlier
reading of it.

### Why SF does not backfill

`catchup=False`, deliberately. A backfill would re-fetch settlement days the II
DAG has already stored — and at a *more mature* run type, since the II backfill
captured whatever was current when it ran rather than II itself. Every row would
conflict and nothing would insert.

> **A backfill does not capture the run its DAG is named after.** All 354 II runs
> executed within a few hours of each other, so each fetched whatever was current
> on the day the backfill ran. Only settlement days from about 2026-08-02 hold
> genuine `II`; everything older is SF, R1, R2 or R3. A DAG's name describes its
> steady-state behaviour, not what its backfill collected — worth knowing before
> writing a model that filters on run type.

## The poll offset is the run-type selector

The API serves only the run currently in force and **discards what it
supersedes** — see [020_b1610.md](020_b1610.md). So the run type is chosen by how
long you wait, not by a parameter:

| Poll at | Returns |
|---|---|
| 14 days | `II` |
| 35 days | `SF` |
| 80 days | `R1` |
| 165 days | `R2` |
| 300 days | `R3` |
| 450 days | `RF` |

That is why this is two DAGs at fixed offsets rather than one job with an
argument, and why **a missed settlement-run revision cannot be recovered
later**. A later rung can still recover the underlying period at a more mature
run type.

## Chunking and volumes

**One day per request**, as PN. Counted after the backfill:

| | |
|---|---|
| Rows fetched per run | 449,673 — 9,177 units x 49 periods |
| Rows stored per settlement day | 440,496 — 48 periods |
| Duplicated per run | 9,177 — one period, absorbed by `ON CONFLICT` |
| Density | 177 bytes/row |

The boundary is inclusive at **both** ends, so a UTC-day window returns 49
periods. Periods 1 and 2 of each settlement day arrive in the *previous* run, 3
to 48 in its own, and period 3 arrives twice.

**That daily overlap is why `retrieved_at` is not in the key.** With it, those
9,177 rows would be stored again every single day — 3.3M redundant rows a year.
Verified in dev: the same window loaded twice leaves the row count unchanged.

### Storage

At 48 periods per settlement day and a mean of 8,234 units across the year:

| Strategy | Rows/year | Size |
|---|---|---|
| Single backfill pass | 144M | ~26 GB |
| **II + SF, as currently implemented** | 321M | **~57 GB** |
| First and final, II + RF | 284M | ~50 GB |
| All six rungs | 879M | ~156 GB |

## Change-only append, not yet adopted

**75.3% of rows are zero**, and a metered zero is unlikely to move between runs.
Storing it once per rung is storing the same nothing repeatedly.

The alternative is to insert only rows whose `quantity` differs from the most
recent stored value for that unit-period. Append-only is preserved — nothing
updated, nothing deleted — but each row costs an index lookup on insert instead
of a blind append, and storage collapses to the initial load plus genuine
changes.

**Deferred rather than rejected, because the change rate cannot be measured from
history.** The II and SF DAGs now run daily and preserve both positions. Once the
same unit-periods have been captured at both run types, their measured difference
will determine whether change-only append is worthwhile.

**Both designs share the same table and key**, so switching later needs no
migration — only a change to the poller's insert.

## Backfill verification

Same shape as PN's: the answer is predictable from a calendar before the query
runs, which is what makes it a test.

```sql
SELECT settlement_date, count(DISTINCT settlement_period) AS periods
FROM raw.elexon_b1610
GROUP BY settlement_date
HAVING count(DISTINCT settlement_period) <> 48
ORDER BY settlement_date;
```

Result for the 2025-08-22 backfill — exactly four rows, all predicted:

| Date | Periods | Why |
|---|---|---|
| 2025-08-22 | 46 | first run; periods 1 and 2 precede the earliest window |
| 2025-10-26 | **50** | clocks go back, 25-hour local day |
| 2026-03-29 | 46 | clocks go forward, 23-hour local day |
| 2026-08-10 | 3 | current head |

**The 50 is the load-bearing one.** This table keys on `settlement_date` and
`settlement_period` directly rather than on a UTC timestamp, so if the repeated
hour had collapsed you would see 48 and would have silently lost an hour of
metered output across 9,000 units.

Note the first day shows **46** here where PN's shows 47 — the two datasets
timestamp differently (`timeFrom` versus `halfHourEndTime`), so the same `from`
parameter lands on a different boundary.

## Unexpected row-count safeguard is outstanding

The response contract now rejects a zero-row result and raises for Airflow
retry before parsing, loading or quarantine. It does not yet detect a non-empty
response whose returned rows satisfy the schema but whose total row count is
unexpectedly low. Those returned rows would load normally; absent rows have no
payload to quarantine. Given B1610's volume, that is the remaining
silent-truncation risk identified in
[../ingestion-patterns.md](../ingestion-patterns.md).
