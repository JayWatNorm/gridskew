# Wind forecast and outturn ingestion

How `raw.elexon_windfor` and `raw.elexon_fuelhh` are loaded. For what the
data means, see [100_wind.md](100_wind.md). For the reasoning behind these
patterns, see [../ingestion-patterns.md](../ingestion-patterns.md).

**One-off back-fill, no DAG.** It holds a year of history for the Q30
analysis (`dbt/analyses/q30_wind_vs_carbon_error.sql`): WINDFOR publish days
20 August 2025 to 16 August 2026 and FUELHH WIND settlement dates 22 August
2025 to 16 August 2026.

| | |
|---|---|
| Module | `ingestion/elexon/wind_poller.py` |
| Back-fill | `ingestion/elexon/wind_backfill.py` |
| Tables | `raw.elexon_windfor`, `raw.elexon_fuelhh` |
| DDL | `sql/migrations/V012__elexon_wind.sql` |
| Tests | `tests/test_elexon_wind.py` |
| dbt | `stg_elexon__windfor`, `stg_elexon__fuelhh`, `int_wind_error_by_period` |

## What is captured

Elexon keeps every WINDFOR issue, so a past year can be read as it was
forecast at the time. A request for publish day 22 August 2025 returned
eight issues of 73 hourly points each, 584 rows. FUELHH, asked for one
settlement date and fuel type WIND, returned one row per settlement period.

`raw.elexon_windfor` keeps every issue: `publish_time` is part of its key.
`raw.elexon_fuelhh` keeps every publication: `publish_time` is part of its
key too, so a later value for a period lands beside the first. In both,
`retrieved_at` records when a row was first stored, and fetching a window or
a date again inserts nothing.

## One request at a time

`run_windfor(conn, publish_from, publish_to)` asks `/datasets/WINDFOR` for
one publish-time window, with `publishDateTimeFrom` and `publishDateTimeTo`
in UTC. `run_fuelhh(conn, settlement_date)` asks `/datasets/FUELHH` for one
settlement date, with `settlementDateFrom` and `settlementDateTo` both that
date and `fuelType=WIND`. Neither endpoint documents a maximum range, so
each request covers one day.

The response must be a dictionary holding a `data` list; an empty list loads
nothing. A row the contract rejects, such as a point with a null
`generation`, is quarantined, and then no row of the request is loaded, so a
day is stored whole or not at all. The request then fails with
`ContractRejected`, which stops the back-fill.

## The back-fill

The back-fill walks WINDFOR's UTC publish days from two days before the
first settlement date read (so the last issue before its first half-hour,
which starts at 23:00 UTC on 21 August 2025, is held) to the last settlement
date, then FUELHH's settlement dates. One request at a time, a second apart,
WINDFOR first:

| Dataset | Unit per request | Requests | Rows, about |
|---|---|---|---|
| WINDFOR | one UTC publish day | 362 | 211,000 |
| FUELHH WIND | one settlement date | 360 | 17,280 |
| **Total** | | **722** | **228,000** |

At a second apart the run takes about 15 to 25 minutes and transfers about
27 MB. Each request prints a summary line.

Elexon publishes no request rate for the Insights API and returns no
rate-limit headers. Its [licence to use BMRS APIs](https://www.elexon.co.uk/bsc/data/balancing-mechanism-reporting-agent/copyright-licence-use-bmrs-api/)
counts calling more than once a half hour for data that changes half-hourly
as misuse, and lets Elexon block users who script large volumes without
notice. The back-fill asks for each day once, one request at a time.

**A WINDFOR publish day is held when it stores at least 8 distinct publish
times.** In the summer and winter samples all eight issues fell inside the
UTC day. **A FUELHH settlement date is held when it stores exactly its
calendar's number of distinct periods**: 46, 48 or 50. A held day or date is
skipped, so running the back-fill again fetches only those still missing or
short, including one a stopped run left part-way. Whether one request
returns all 50 periods of an autumn clock-change date is untested; if it
does not, the date stays not held and is listed.

Run it in the Airflow container, which holds the `gridskew_prod` connection,
after V012 is applied:

```bash
python -m ingestion.elexon.wind_backfill --dry-run
python -m ingestion.elexon.wind_backfill
```

The dry run makes no request: it prints, for each dataset, its first and
last day, the days held, the days it would fetch, and the total requests to
make (722 on an empty table).

## When the back-fill stops

The first failed request stops the run: no further request is made. It
prints one line naming the dataset, the day and the reason, then the final
report, and exits with code 1.

| Reason | What it means | Before running again |
|---|---|---|
| `rate limited (HTTP 429)` | Elexon refused the request for its rate | Wait: no retry time is published or returned. If it happens again, leave the back-fill and raise the pause first |
| `server error (HTTP 5xx)` | Elexon failed to answer | Run again later, once the API answers |
| `request refused (HTTP 4xx)` | The request itself was refused (any 4xx but 429) | Read the error; a wrong request fails every day the same way |
| `quarantined rows` | A row broke the contract; no row of that day is stored | Read the day's rows in `raw.endpoint_quarantine`; do not run again until the cause is known, or the same day is quarantined again |
| `other failure` | No answer (timeout, connection) or a database error | Read the error |

A source that has started to refuse or misbehave only gets worse with more
requests, and a re-run costs nothing, so the run stops on any failure rather
than skipping the day.

The final report lists, for each dataset: the days held before the run, the
days requested, rows stored, the failed day with its reason, the days held
after the run, and the days still not held. A day the source served short is
not held but is not a failure; the coverage check before the analysis is
read records it.

**To resume**, run the dry run, check that its days to fetch match the
report's days still not held, then run the back-fill again. It skips every
held day and starts from the first day not held, so the stopped day is asked
for again.

## Freshness

None. The tables grow only when the back-fill runs, so the dbt sources carry
no freshness check.
