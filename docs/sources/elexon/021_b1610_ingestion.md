# B1610 ingestion

How `raw.elexon_b1610` is loaded. For what the data means and what the columns
are, see [020_b1610.md](020_b1610.md). For the reasoning behind these patterns,
see [../ingestion-patterns.md](../ingestion-patterns.md).

**Status: II and SF rungs deployed.** The II backfill completed 2026-08-23:
354 runs covering settlement days 2025-08-22 to 2026-08-10, with 139,316,985
rows at that checkpoint and no unexpected gaps. Scheduled II and SF capture
then continued; the exact table total was **159,151,689 rows on 2026-09-21**.

For the 2026-08-10 through 2026-08-16 cohort, II and SF each contain
3,083,376 rows with periods 1–48 present on every date. R1 for the same dates
is captured from 2026-10-05 through 2026-10-11.

Endpoint validation and quarantine are deployed. The poller's Decimal-aware
JSON encoder needs `simplejson`, which the Airflow image includes.

| | |
|---|---|
| Module | `ingestion/elexon/b1610_poller.py` |
| Table | `raw.elexon_b1610` |
| DDL | `sql/init/005_elexon_b1610.sql` |
| Tests | `tests/test_elexon_b1610.py`, against `b1610_stream.json` |
| DAGs | `gridskew_elexon_b1610_II_dag.py`, `..._SF_dag.py`, `..._cohort_dag.py` |
| Schedule | II/SF: `@daily`; cohort: seven fixed events at 06:00 UTC |
| `catchup` | II **`True`**, SF **`False`** |

## Validation and quarantine

B1610 requires a non-empty list response and validates every item before typed
parsing. It uses the same routing as PN and QPN. Its source adapter supplies a
Decimal-aware encoder so rejected quantities remain JSON numbers without
conversion to binary floats or strings. See
[../endpoint-validation.md](../endpoint-validation.md).

## Two standing rungs currently implemented

| Rung | Settlement date captured | `start_date` | Requests |
|---|---|---|---|
| **II** | `data_interval_start - 14d` | 2025-09-05 | `II` |
| **SF** | `data_interval_start - 35d` | forward only | `SF` |

Each daily run captures one British settlement date, names its run in the
request and then checks what is stored. See
[One settlement date per run](#one-settlement-date-per-run).

`R1` through `RF` are not standing full-history rungs. They can be added without
changing the schema, but only prospectively: Elexon stops serving a run after a
later run supersedes it. Any R1, R2, R3 or RF capture already missed cannot be
recovered. Until a final-run schedule exists, the retained series is accurately
described as the first and latest captured positions, not the first and final
positions.

There is one bounded exception. The seven settlement dates from 2026-08-10
through 2026-08-16 are requested at fixed +56 days, on 2026-10-05 through
2026-10-11. Completion requires every response to report `R1`; this cohort
measures II→SF and SF→R1 restatement without committing to full-history
R1/R2/R3 storage.

**`start_date` is the earliest wanted settlement day plus the offset.** 2025-09-05
minus 14 days is 2025-08-22, which aligns B1610 with PN's history. A different
calculation would misalign the two tables used by the thesis join.

### Why 14 days rather than 7

II is live from day 7, but publication counts **working** days. Five working days
is seven calendar days in a normal week and about twelve around Christmas.

The poller rejects an empty response and fails visibly, so retries can
recover a transient publication delay. The 14-day offset gives a working-day
publication margin at the cost of a week of freshness. It is a margin, not a
guarantee: the capture check below fails a run whose settlement date is not
yet fully published.

The 35-day rung can capture a later settlement run for a date missed by the
head poll. It does not recover the missing earlier II reading: settlement
runs that the endpoint has replaced may no longer be retrievable. Record
any missing or unexpected run type rather than treating SF as equivalent
evidence for II.

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

## One settlement date per run

The daily II and SF runs and the bounded cohort all call
`capture_settlement_date` with one British settlement date and one run type.

The request names the run (`settlementRunType`). The British-day window
includes half-hour **start** times, despite the response field being named
`halfHourEndTime`. The inclusive API bounds are local midnight through the
next local midnight minus 30 minutes, converted to UTC. For 2026-08-10 this is
`2026-08-09T23:00Z` through `2026-08-10T22:30Z`. Bounds built from period end
times would skip period 1 and include the first period of the following
settlement date. Clock-change days have 46 or 50 periods rather than 48.

Before routing or writing, the capture refuses a response containing a typed
settlement date different from the requested date or an integer period outside
1 through that date's expected count. Either mismatch rejects the entire
batch. Missing or wrongly typed fields still follow the existing quarantine
route.

After compatible rows commit, the task requires every returned row to have the
requested run type and the stored period numbers for that settlement date and
run type to be exactly 1–46, 1–48 or 1–50 as appropriate. It logs the
requested date and run, returned run counts, stored rows, periods and units.
Mixed runs or missing or unexpected stored periods fail visibly while keeping
already committed data. The existing key makes a repeated insert idempotent.
This check is period coverage; it does not prove every unit is complete.

A run whose settlement date is not yet fully published therefore fails
instead of succeeding with part of the date. A run asked for a superseded run
type receives an empty response and fails.

**Clear a task instance to repeat a capture**, while the run is still in
force. Do not manually trigger an old date: manual triggering is not a
reliable way to select a past capture. A run without a data interval or
logical date is refused.

The stored-coverage query filters on `settlement_date`; the block-range index
`idx_raw_elexon_b1610_settlement_date` lets it read one date's blocks.

## Bounded cohort captures

`gridskew_elexon_b1610_cohort` captures R1 for British settlement dates
2026-08-10 through 2026-08-16. Its only seven events are 2026-10-05 through
2026-10-11 at 06:00 UTC, each settlement date +56 days. It starts paused,
uses the existing `elexon` pool and enables catchup for a late unpause.
The date mapping in the DAG is the schedule's sole source; an unlisted date
is refused.

RF is a later addition to the same bounded mapping: +430 days,
2027-10-14 through 2027-10-20. Those RF events are not yet scheduled. A filter
cannot restore a run that the source has superseded.

## The poll offset determines which run is available

The API serves only the run currently in force and **discards what it
supersedes** — see [020_b1610.md](020_b1610.md). So the run type is chosen by how
long you wait. Every capture requests its run explicitly; the completion
check still verifies what the source returned:

| Poll at | Returns |
|---|---|
| 14 days | `II` |
| 35 days | `SF` |
| 80 days | `R1` (standing-rung margin; the bounded cohort requests +56 days) |
| 165 days | `R2` |
| 300 days | `R3` |
| 450 days | `RF` |

II and SF therefore remain two standing DAGs at fixed offsets; each names its
run and checks the returned run, as the bounded cohort does.
**A missed settlement-run revision cannot be recovered later** once the source
supersedes it. A later rung can still recover the underlying period at a more
mature run type.

## Chunking and volumes

**One settlement date per request.** Counted after the backfill:

| | |
|---|---|
| Rows fetched and stored per run | 440,496 — 9,177 units x 48 periods |
| Density | 177 bytes/row |

The boundary is inclusive at **both** ends. The backfill and the daily runs
before the one-date windows used UTC-day windows, which return 49 periods:
periods 3 to 48 of one settlement day, periods 1 and 2 of the next, and
period 3 twice.

**`retrieved_at` is not in the key**, so a repeated capture of the same run
stores nothing. Verified in dev: the same window loaded twice leaves the row
count unchanged. That is what makes clearing a failed capture safe.

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

## Period coverage is checked; unit volume is not

The response contract rejects a zero-row result and raises for Airflow retry
before parsing, loading or quarantine. Each capture then fails when its
settlement date is stored without every settlement period. It does not detect
a response that holds every period but too few units: those rows load
normally, and absent rows have no payload to quarantine. That remainder is the
silent-truncation risk described in
[../ingestion-patterns.md](../ingestion-patterns.md).
