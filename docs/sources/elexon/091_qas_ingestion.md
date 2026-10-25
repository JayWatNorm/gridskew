# QAS ingestion

How `raw.elexon_qas` is loaded. For what the data means, see
[090_qas.md](090_qas.md). For the reasoning behind these patterns, see
[../ingestion-patterns.md](../ingestion-patterns.md).

**Status: deployed 16 October 2026; backfill 22 August 2025 to 5 October
2026; daily captures from 21 October 2026.**

| | |
|---|---|
| Module | `ingestion/elexon/qas_poller.py` |
| Backfill | `ingestion/elexon/qas_backfill.py` |
| Table | `raw.elexon_qas` |
| DDL | `sql/migrations/V010__elexon_qas.sql` |
| Tests | `tests/test_elexon_qas.py`, `tests/test_elexon_qas_dag.py`, `tests/test_elexon_qas_backfill.py`, against `qas_volumes.json` |
| DAG | `dags/gridskew_elexon_qas_dag.py` |
| Schedule | daily at 00:00 UTC, with a full-day data interval |
| `start_date` | 2026-10-20 |
| `catchup` | `True` |

## One settlement date per request

`run(conn, settlement_date)` asks for one date with
`settlementPeriodFrom=1&settlementPeriodTo=50`, so `from` and `to` select by
settlement date. A 50-period date is asked for in two requests (periods 1
to 25 and 26 to 50), because Elexon refuses a request whose start times
span more than a day; the two are one capture with one `retrieved_at`.
Before anything is written, the response must be one whole
date: every row carries the requested date, a period of that date (46, 48 or
50), and a unit and period no other row has, and every period has at least
one row. A response that fails is refused and the try is repeated.

A row the contract rejects is quarantined as usual, but then no row of the
response is loaded: a missing unit reads as no ABSVD, so a partly loaded
date would be wrong. The task fails without a retry, as for any contract
rejection.

## Three captures of every date

The DAG has three tasks, capturing the settlement dates 14, 35 and 450 days
before each run's data interval, the ages of the B1610 II, SF and RF
captures. NESO sends QAS by the second business day, so every target is past
publication and an empty response is a failed capture. Each task retries for
about four days. A target before 22 August 2025, the first settlement date
GridSkew holds, is skipped.

Every capture is kept: `retrieved_at` is part of the key, and the source has
no version to compare. `int_shortfall_by_unit_period` reads each unit-period
from the latest capture that holds it. `dbt/analyses/qas_resubmissions.sql`
counts, for dates captured more than once, the unit-periods that changed,
appeared or were dropped between captures.

## The backfill

The dates before the DAG's first 14-day target (22 August 2025 to 5 October
2026, 410 dates) come from a one-off run of the same poller, in the Airflow
container:

```bash
python -m ingestion.elexon.qas_backfill --dry-run
python -m ingestion.elexon.qas_backfill
```

One request at a time, a second apart. A date already stored is skipped, so a
second run repeats only the dates still missing. A failed date is reported and
the run goes on; the exit code is 1 when any date failed. The summary names
the smallest day and the fewest rows in any period.

The backfill loads outside Airflow, so the shortfall table needs a full
refresh after it.

## Freshness

`warn_after` 25 hours, no error: each capture retries for about four days.
