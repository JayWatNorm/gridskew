# Wind forecast and outturn ingestion

How `raw.elexon_windfor` and `raw.elexon_fuelhh` are loaded. For what the
data means, see [100_wind.md](100_wind.md). For the reasoning behind these
patterns, see [../ingestion-patterns.md](../ingestion-patterns.md).

**A daily capture from 1 November 2026, beside a one-off back-fill of a
year.** The DAG `gridskew_elexon_wind` captures every publish day and
settlement date from 1 November 2026 for Q36. The back-fill holds a year of
history for the Q30 analysis (`dbt/analyses/q30_wind_vs_carbon_error.sql`):
WINDFOR publish days 20 August 2025 to 16 August 2026 and FUELHH WIND
settlement dates 22 August 2025 to 16 August 2026. The days between are not
held.

| | |
|---|---|
| Module | `ingestion/elexon/wind_poller.py` |
| DAG | `dags/gridskew_elexon_wind_dag.py` (`gridskew_elexon_wind`) |
| Back-fill | `ingestion/elexon/wind_backfill.py` |
| Tables | `raw.elexon_windfor`, `raw.elexon_fuelhh` |
| DDL | `sql/migrations/V012__elexon_wind.sql` |
| Tests | `tests/test_elexon_wind.py`, `tests/test_elexon_wind_dag.py` |
| dbt | `stg_elexon__windfor`, `stg_elexon__fuelhh`, `int_wind_error_by_period`, `warn_wind_capture_days_short` |

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
`ContractRejected`.

## Daily capture

The DAG runs at 06:00 UTC. Each run targets the previous UTC day: its WINDFOR
publish day, the UTC date of the issues' source stamps, and the settlement
date of the same name (at 06:00 UTC the London date is the UTC date in winter
and in summer). By then the day's last issue, stamped 22:30 or 23:30, is out,
and the date's last period ended six or seven hours earlier. Days are
captured from 1 November 2026; a target before then asks for nothing.

Four tasks share the one-slot `elexon` pool, so requests go one at a time,
and none waits for another:

| Task | Asks for | On a failed request |
|---|---|---|
| `poll_windfor`, `poll_fuelhh` | The target, unless it is complete or held in quarantine | The try fails. Airflow retries it 5 times, waiting 30 minutes, then doubling up to 2 hours; each try asks for the target again only while it is not complete, and never for an older day |
| `poll_windfor_look_back`, `poll_fuelhh_look_back` | Each of the seven days before the target, from 1 November 2026, that is neither complete nor held: once each, newest first, a second apart | It logs the day and asks for the next, so one failing day never blocks an older one. It stops at once on HTTP 429 or a database error. After its last request it fails, naming the failed days. It has no retries: the next morning's run asks again |

**Complete and not complete.** A day either is complete or is not; a short
day (some rows) and a missing day (no rows) are treated alike.

- A WINDFOR publish day is complete when it stores at least 8 issues stamped
  in that UTC day and every one of them holds a point for each hour from
  21:00 UK time the day before to 21:00 UK time two days after: 73 points,
  or 72 and 74 on the three publish days a year whose span holds a clock
  change. Every issue of 24 October 2026 held 74 points and every issue of
  28 March 2026 held 72.
- A FUELHH settlement date is complete when its stored WIND periods are
  exactly 1 to its 46, 48 or 50. A period published twice counts once; a
  date holding periods 1 to 47 and 49 is not complete.

A day is about 632 rows: 584 for a publish day (8 issues of 73 points; 576
or 592 on the clock-change days) and 46, 48 or 50 for a settlement date.

**A short or empty reply is not a failure.** The target task succeeds, and
the next morning's look-back asks for the day again. A day the look-back
leaves not complete on its seventh morning is logged as a warning: no
scheduled run asks for it again.

**A quarantined day.** On a rejected row the request stores only the rejected
rows, in `raw.endpoint_quarantine`, and nothing of that day in the raw
table. The day is then held: the quarantine holds a row of its dataset whose
`request_context` names it (`publishDateTimeFrom` for WINDFOR, read as a UTC
instant; `settlementDate` for FUELHH). Neither task asks for a held day,
since the same rows would be quarantined again. Instead the task fails
without a retry each morning while the held day is its target or in its
look-back, after asking for its other days. To release a held day:

1. Read the day's `validation_errors` and `payload` in the quarantine
   ([operations](../../operations.md#the-quarantine)).
2. If Elexon legitimately sends a null `generation`, the contract, the
   tables' `NOT NULL` and the Q36 analysis must change together before the
   day can load.
3. Delete that day's quarantine rows, by `dataset` and the day in
   `request_context`, after counting them.
4. The next run asks for the day while it is in the look-back; for an older
   day, backfill the run whose target it is
   ([operations](../../operations.md#running-past-days-again)).

**Request bounds.** Each run makes 1 request per dataset on a normal day.
Counting every try, a run makes at most 13 per dataset, 26 in all: 6 tries of
the target and 7 older days, all of them failing only when the API is down
for the whole morning after an outage. A day is asked for at most 13 times
over its life: 6 tries as the target and once a day for seven days after.
On the schedule no day is asked for twice within 30 minutes; a backfill of
several runs can ask a short day again within minutes
([operations](../../operations.md#running-past-days-again)). A gap older
than seven days needs `airflow backfill create`.

**Each task logs one JSON line**: the dataset, the task, the target and the
range, the days asked for and the rows each returned, the days whose request
failed, the days quarantined or held, and the days still not complete.

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
short, including one a stopped run left part-way. One request for
the autumn clock-change date 25 October 2026 returned all 50 periods; a
date that returns fewer stays not held and is listed.

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

Both sources warn when no row has been stored for 25 hours and fail after
48, in the hourly freshness check: a warning after one morning's capture
stored nothing, a failure after two.

The nightly test `warn_wind_capture_days_short` lists each publish day and
settlement date that is not complete, from 1 November 2026 or the last 30 UTC
days, whichever is later, up to two days ago (the last two may not be
captured when the nightly runs). Each row gives the issues or periods found
and expected and, for WINDFOR, the smallest issue's points against the
points expected. It reads counts only and is a warning: it lands in
`dbt_dev.dbt_node_results` and never fails the nightly.
