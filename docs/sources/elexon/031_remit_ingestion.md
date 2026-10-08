# REMIT ingestion

How `raw.elexon_remit` is loaded. For what the data means, see
[030_remit.md](030_remit.md). For the reasoning behind these patterns, see
[../ingestion-patterns.md](../ingestion-patterns.md).

**Status: built; not yet released.**

| | |
|---|---|
| Module | `ingestion/elexon/remit_poller.py` |
| Table | `raw.elexon_remit` |
| DDL | `sql/migrations/V009__elexon_remit.sql` |
| Tests | `tests/test_elexon_remit.py`, against `remit_publications.json` |
| DAG | `dags/gridskew_elexon_remit_dag.py` |
| Schedule | hourly at :15 |
| `catchup` | `False` |

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
from the newest day stored. The API served 2020-06-01 when asked on
2026-10-07, so the start date is a choice and not a limit of the source.

The request includes both ends of its window and filters on the full publish
time. The one-hour step back re-reads messages already stored; the key makes
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
and the compatible rows are loaded. The run then fails, and so does every
later run while any `REMIT` row is in `raw.endpoint_quarantine`. Each of
those runs still loads the new messages first.

The failure has to last because the window moves on. A later run starts
after the rejected message and never requests it again, so a failure only in
the run that met the row would be followed by a success with the message
still missing.

To clear it:

1. Read each row's `validation_errors` and correct the contract or the
   parser.
2. Load the range again from the earliest `publishDateTimeFrom` in the
   quarantined rows' `request_context`:
   `run(conn, first_publish_time=<that time>)`. The key makes the rows
   already stored a no-op. This run also fails, because the quarantine is
   not yet empty; a row it rejects is quarantined again.
3. Delete only the quarantined rows whose message is now stored:

   ```sql
   DELETE FROM raw.endpoint_quarantine AS quarantined
   USING raw.elexon_remit AS stored
   WHERE quarantined.dataset = 'REMIT'
     AND stored.mrid = quarantined.payload ->> 'mrid'
     AND stored.revision_number::text = quarantined.payload ->> 'revisionNumber'
     AND to_char(stored.publish_time AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
         = quarantined.payload ->> 'publishTime'
     AND to_char(stored.created_time AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
         = quarantined.payload ->> 'createdTime';
   ```

   The statement compares text and converts nothing from the payload, so a
   quarantined row with a malformed or missing key field cannot stop it;
   that row matches nothing and stays.

A row that remains was not recovered: its message is still missing, and the
run keeps failing for it. Never delete a `REMIT` quarantine row for any
other reason than that its message is stored, or that the message is
knowingly given up.

Most optional fields are absent on some messages, so each run logs one
warning for each optional field that a window lacks.

## Freshness

`dbt source freshness` reads `publish_time`: it warns after 12 hours without
a new message and fails after 24. The longest gap between two messages in
September 2026 was three and a half hours.

## Not proven

A message that appears in the API more than one hour after its own
`publish_time` would be missed. None has been looked for: that needs two
reads of the same window some time apart.
