# B1610 ingestion

How `raw.elexon_b1610` is loaded. For what the data means and what the columns
are, see [020_b1610.md](020_b1610.md). For the reasoning behind these patterns,
see [../ingestion-patterns.md](../ingestion-patterns.md).

**Status: II and SF rungs deployed; the RF rung starts on 2026-11-15.** The II
backfill covers settlement days 2025-08-22 to 2026-08-10 in 354 runs, with no
unexpected gaps; scheduled II and SF capture continues from there.

For the 2026-08-10 through 2026-08-16 cohort, II, SF and R1 each contain
3,083,376 rows with periods 1–48 present on every date.

Endpoint validation and quarantine are deployed. The poller's Decimal-aware
JSON encoder needs `simplejson`, pinned in `requirements.txt`.

| | |
|---|---|
| Module | `ingestion/elexon/b1610_poller.py` |
| Table | `raw.elexon_b1610` |
| DDL | `sql/migrations/V005__elexon_b1610.sql` |
| Tests | `tests/test_elexon_b1610.py`, against `b1610_stream.json` |
| DAGs | `gridskew_elexon_b1610_II_dag.py`, `..._SF_dag.py`, `..._RF_dag.py` |
| Schedule | daily at 00:00 UTC |
| `catchup` | `True` |
| Retries | 16, backing off to 8 hours: about four days |

## Validation and quarantine

B1610 requires a non-empty list response and validates every item before typed
parsing. It uses the same routing as PN and QPN. Its source adapter supplies a
Decimal-aware encoder so rejected quantities remain JSON numbers without
conversion to binary floats or strings. See
[../endpoint-validation.md](../endpoint-validation.md).

## Three standing rungs

| Rung | Settlement date captured | `start_date` | Requests |
|---|---|---|---|
| **II** | `data_interval_start - 14d` | 2025-09-05 | `II` |
| **SF** | `data_interval_start - 35d` | 2025-09-26 | `SF` |
| **RF** | `data_interval_start - 450d` | 2026-11-15 | `RF` |

Each daily run captures one British settlement date, names its run in the
request and then checks what is stored. See
[One settlement date per run](#one-settlement-date-per-run).

The three rungs keep the first (II), the settled (SF) and the final (RF)
position of every settlement date; analysis compares the first with the
latest held. RF is the last run and stays in force once published, so its
450-day offset needs no margin: it is after RF under today's 14-month
timetable and under any shorter one. `R1` to `R3` are not standing rungs;
Elexon stops serving a run once a later run replaces it, so a run not
captured in its window cannot be recovered.

Before downloading, each capture asks which run Elexon serves for the date
(one settlement period, `/datasets/B1610`). If a later run has already
replaced the wanted one, the task fails at once without retrying, since the
run can no longer be captured; this keeps catch-up after a long outage from
spending days on dates already lost. Otherwise a failing capture retries for
about four days. Each run's offset leaves at
least that long before the run is replaced (II is in force to about day 21,
SF to about day 42), and `max_active_runs=3` lets later dates proceed while
one date retries; the single `elexon` pool slot still keeps requests one at a
time. After an outage the scheduler creates the missed runs (`catchup=True`);
those whose run is still in force are captured.

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

The poller rejects an empty response and fails visibly, so a retry repeats
the capture once the date is published. The
14-day offset gives a working-day publication margin at the cost of a week of
freshness. It is a margin, not a
guarantee: the capture check below fails a run whose settlement date is not
yet fully published.

The 35-day rung can capture a later settlement run for a date missed by the
head poll. It does not recover the missing earlier II reading: settlement
runs that the endpoint has replaced are no longer retrievable. Record
any missing or unexpected run type rather than treating SF as equivalent
evidence for II.

### SF catch-up

After an outage, catch-up creates SF runs for the missed dates. Those still
within about 42 days capture SF; older ones receive an empty response and
fail, because SF has been superseded for them. The II backfill already stored
the SF rows that were current when it ran.

> **The II backfill holds whichever run was in force when it ran.** It did not
> name a run type, and all 354 runs executed within a few hours of each other,
> so only settlement days from about 2026-08-02 hold `II`; older days hold SF,
> R1, R2 or R3.

## One settlement date per run

The daily II, SF and RF runs all call
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
batch. Missing or wrongly typed fields follow the quarantine route.

After compatible rows commit, the task requires every returned row to have the
requested run type and the stored period numbers for that settlement date and
run type to be exactly 1–46, 1–48 or 1–50 as appropriate. It logs the
requested date and run, returned run counts, stored rows, periods and units.
Mixed runs or missing or unexpected stored periods fail visibly while keeping
already committed data. The primary key makes a repeated insert idempotent.
This check is period coverage; it does not prove every unit is complete.

A run whose settlement date is not yet fully published therefore fails
instead of succeeding with part of the date. A run asked for a superseded run
type receives an empty response and fails.

A capture still failing after its retries can be repeated by clearing the
task instance while the run is in force (RF: at any time). Do not manually
trigger an old date: a run without a data interval or logical date is
refused.

The stored-coverage query filters on `settlement_date`; the block-range index
`idx_raw_elexon_b1610_settlement_date` lets it read one date's blocks.

## The R1 cohort week

R1 for British settlement dates 2026-08-10 through 2026-08-16 was captured
once, on 2026-10-05 through 2026-10-11 (each date +56 days), so that week
holds II, SF and R1 and measures restatement step by step. RF for those
dates comes from the RF rung, like every other date.

## The poll offset determines which run is available

The API serves only the run currently in force and **discards what it
supersedes** — see [020_b1610.md](020_b1610.md). So the run type is chosen by how
long you wait. Every capture requests its run explicitly; the completion
check still verifies what the source returned:

| Poll at | Returns |
|---|---|
| 14 days | `II` |
| 35 days | `SF` |
| 80 days | `R1` |
| 165 days | `R2` |
| 300 days | `R3` |
| 450 days | `RF` |

II, SF and RF are therefore three standing DAGs at fixed offsets; each names
its run and checks the returned run.

## Chunking and volumes

**One settlement date per request.** For a 48-period date:

| | |
|---|---|
| Rows fetched and stored per run | 440,496 — 9,177 units x 48 periods |
| Density | 177 bytes/row |

The boundary is inclusive at **both** ends. UTC-day windows, used by the
backfill and the earlier daily runs, return 49 periods: periods 3 to 48 of one
settlement day, periods 1 and 2 of the next, and period 3 twice.

**`retrieved_at` is not in the key**, so a repeated capture of the same run
stores nothing. Loading the same window twice leaves the row count unchanged.
That is what makes clearing a failed capture safe.

### Storage

Each figure counts 48 periods per settlement day. The single pass uses the
year's mean of 8,234 units; each rung uses the unit count at its age (9,177 at
II and SF):

| Strategy | Rows/year | Size |
|---|---|---|
| Single backfill pass | 144M | ~26 GB |
| II + SF | 321M | ~57 GB |
| First and final, II + RF | 284M | ~50 GB |
| **II + SF + RF, as implemented** | about 445M | **about 79 GB** |
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
history.** The II, SF and RF captures, and the R1 cohort, hold the same
unit-periods at more than one run type; their measured difference determines
whether change-only append is worthwhile.

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
| 2026-08-10 | 3 | last backfill window; periods 1 to 3 only |

**The 50 is the load-bearing one.** This table keys on `settlement_date` and
`settlement_period` directly rather than on a UTC timestamp, so if the repeated
hour had collapsed you would see 48 and would have silently lost an hour of
metered output across 9,000 units.

Note the first day shows **46** here where PN's shows 47 — the two datasets
timestamp differently (`timeFrom` versus `halfHourEndTime`), so the same `from`
parameter lands on a different boundary.

## Period and unit coverage are checked

The response contract rejects a zero-row result and raises for Airflow retry
before parsing, loading or quarantine. Each capture then fails when its
settlement date is stored without every settlement period, or with fewer than
95% of the units that the same run held on its best day of the previous seven
settlement dates. The best day, not the day before, so small drops cannot add
up from day to day. A date with no earlier date of its run in that week, such
as the first RF capture, is checked for periods only.
