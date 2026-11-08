# REMIT ingestion

How `raw.elexon_remit` is loaded. For what the data means, see
[030_remit.md](030_remit.md). For the reasoning behind these patterns, see
[../ingestion-patterns.md](../ingestion-patterns.md).

**Status: deployed, history loaded 2026-10-08.** 107,941 messages published
from 2024-08-22 to that day.

| | |
|---|---|
| Module | `ingestion/elexon/remit_poller.py` |
| Table | `raw.elexon_remit` |
| DDL | `sql/migrations/V009__elexon_remit.sql` |
| Tests | `tests/test_elexon_remit.py`, `tests/test_elexon_remit_reread.py`, against `remit_publications.json` |
| DAG | `dags/gridskew_elexon_remit_dag.py`; the daily re-read `dags/gridskew_elexon_remit_reread_dag.py` |
| Schedule | hourly at :15; the re-read daily at 04:40 UTC |
| `catchup` | `False` |
| dbt | `stg_elexon__remit`, `warn_remit_stored_late` |

## The window comes from the table

`run(conn)` takes no window. It reads the newest stored `publish_time`,
steps back one hour, and requests everything published from there to now, in
windows of at most one day. The DAG carries no dates, so a run after missed
runs fetches the whole gap, and a backfill and an hourly poll are the same
code.

A row whose `publish_time` is later than its own `retrieved_at` was dated in
the future when it was stored. Such a row is never used to choose the
window, so it cannot move the window past the messages published before it,
either at once or after a stoppage that lasts beyond its date.

On an empty table the range starts at 2024-08-22, one year before the first
PN day. The first run therefore makes about 780 requests, 0.2 seconds apart,
and commits each day as it goes. If it stops part-way, the next run continues
from the newest message stored. The API served 2020-06-01 when asked on
2026-10-07, so the start date is a choice and not a limit of the source.

The request gives its window to the minute, both ends included. The one-hour
step back re-reads messages already stored; the key makes
that a no-op.

## One row per publication

The key is `(mrid, revision_number, publish_time, created_time)`. The first
two are not enough: see [030_remit.md](030_remit.md#the-key). `retrieved_at`
is not part of the key, so a repeated read inserts nothing and the column
holds the time of the poll that first stored the row.

## Thin columns, whole payload

The table has typed columns for the identifiers, times, type, status, unit
and capacities. The complete source row is kept in `payload` as `jsonb`,
including the fields with no column, such as `outageProfile`,
`messageHeading`, `cause` and `relatedInformation`. Capacities are decoded
as `Decimal`, stored as `numeric`, and written to `payload` as published
(`49.920`).

## An empty window is not a failure

An hour with no REMIT message is normal, so an empty list is accepted. A
response that is not a list fails the run.

## A quarantined row keeps the run failing

Validation and quarantine routing are as in
[011_pn_ingestion.md](011_pn_ingestion.md): a rejected row is quarantined
and the compatible rows are loaded. The run then fails at once, without a
retry, and so does every
later run while `raw.endpoint_quarantine` holds a `REMIT` row whose message
is not stored. Each of those runs still loads the new messages first.

The failure has to last because the window moves on. A later run starts
after the rejected message and never requests it again, so a failure only in
the run that met the row would be followed by a success with the message
still missing.

To clear it:

1. Read each row's `validation_errors` and correct the contract or the
   parser.
2. Load the range again from the earliest `publishDateTimeFrom` in the
   quarantined rows' `request_context`:
   `run(conn, first_publish_time=datetime.fromisoformat('<that value>'))`.
   The key makes the rows already stored a no-op. A quarantined row whose
   message this load stores no longer counts, so the run succeeds once every
   message is stored, without deleting the quarantine rows.

A row is matched to its stored message on `mrid`, `revisionNumber`,
`publishTime` and `createdTime`, compared as text, so a row with a malformed
or missing key field matches nothing and keeps counting.

A row that keeps counting was not recovered: its message is still missing,
and the run keeps failing for it. Delete a `REMIT` quarantine row only when
the message is knowingly given up.

The poller never deletes a quarantine row. What has happened to quarantine
rows so far, and the change under consideration, is in
[operations](../../operations.md#the-quarantine).

A message leaves out a field that has no value, so the run does not log a
warning for an absent optional field. An unexpected field still logs one.

## Freshness

`dbt source freshness` reads `publish_time` and warns after 12 hours without
a new message. It never fails on REMIT: the source has no cadence, and no
message was published on 2026-04-25 or 2026-04-26. The longest gap in
September 2026 was three and a half hours. A message stored before its own
publish time is left out of the check, as it is from the poller's window.

## Daily re-read

`gridskew_elexon_remit_reread` runs at 04:40 UTC and asks again for each of
the seven UTC publish days up to the day before, oldest first, one request a
day, a second apart, in the hourly run's request form. A message already
stored is a no-op under the key. A message the hourly run missed is stored
with `retrieved_at` set to the time of the re-read's request. Each publish
day is read again on the seven mornings after it: a bounded recovery window,
not a complete history.

**It never moves the hourly window.** Before any request it reads the
hourly run's newest stored `publish_time`. A day starting at or after that
time is not asked; a day running past it is asked only up to it; and a row
is stored only if its `publish_time` lies inside its requested day and is
not later than that time. So the re-read never asks for or stores anything
past the hourly run's newest stored message: after an outage that began
before the seven days it asks nothing, and when that message lies inside
the seven days only the days and part of a day before it are asked. The
hourly run's window is the same after a re-read as before. Rows a response returns outside the requested day are
left out and counted, not quarantined.

**What a late row shows.** A row the re-read stores was not in the hourly
run's reads. Its `retrieved_at` says when GridSkew stored it, not when
Elexon began serving it: it may have appeared after the hourly reads that
covered its `publish_time`, or an earlier response may have left it out. A
large gap between `publish_time` and `retrieved_at` can also come from the
hourly run catching up after an outage. Such a row is stored late; it is
never evidence that the message was available at its publish time.

**Failures.** The task has no retry; the next morning's run asks again.

- HTTP 429 or a database error stops it at once. The days not asked stay in
  the following runs' ranges, except the oldest, which is asked first.
- Any other failed request, including a response the parser cannot read (a
  time that is not a time), is rolled back, and the next day is asked; the
  task fails after the last day. A malformed time stores no row of that
  response and writes no quarantine row; the day fails each morning while
  Elexon serves it.
- While a `REMIT` row is in quarantine (above), the re-read makes no request
  and fails. The hold covers every day, so a quarantine left for more than
  seven days leaves the days that pass out of the range unread by schedule;
  once it is cleared, `airflow backfill create` for the runs whose ranges
  hold them reads them.
- A message the contract rejects is quarantined, the remaining days are
  asked, and the task fails; the hourly run then fails too, by its own count.
- A response holding both a row the contract rejects (a value of the wrong
  type) and a malformed time: the rejected rows are quarantined and
  committed first, then the parser fails the day, which is recorded as
  failed with the count of rows quarantined; the task fails after the last
  day, and the following re-read is held until the quarantine is cleared.
  Nothing is lost: no row of that response is stored, the rejected rows are
  kept in the quarantine, and the day is asked again once it is dealt with.

**Record.** Each run logs one JSON summary: the watermark, the days skipped
or shortened by it, the days asked, the rows served, left out and newly
stored per day, how late the newly stored rows were (rounded up to whole
seconds), the failed days and any rows quarantined. The nightly test
`warn_remit_stored_late` lists each UTC publish day, from 9 October 2026 and
within the last 30 days up to yesterday, that holds a message stored more
than two hours after its `publish_time`. The two hours are decided on the
exact interval; the seconds late it shows are rounded up to whole seconds,
so a message 7,200.4 seconds late shows 7201. Counts and seconds only. It
warns and never fails the nightly.

## Limit

A message that Elexon serves more than about an hour after its own
`publish_time` is missed by the hourly read. The daily re-read stores it if
it appears within seven days of its publish day, with `retrieved_at` set to
the re-read's time. That time says when GridSkew stored it, not when Elexon
began serving it. A message that appears later than seven days is missed.
