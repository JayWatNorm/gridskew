# BM-unit registry ingestion

GridSkew polls `GET /reference/bmunits/all` once per day. The endpoint gives
the current registry, with no historical backfill or source update timestamp.
Collection starts an **observed** history; it cannot recover earlier changes.
Capture and the snapshot are deployed. The
[deployment page](../../deployment.md) explains the release boundary.

## Complete-extract gate

The poller requires a successful HTTP response and a non-empty list in which
every row passes the 22-field contract. Each row needs a nonblank National Grid
BM-unit ID. Populated capacity strings must use ordinary decimal/scientific
notation within PostgreSQL numeric limits. Staging trims ASCII whitespace from
capacity strings and treats whitespace-only values as null. A repeated unit ID
is accepted only when its EICs are distinct and populated and every other
source attribute agrees.
The raw table keeps both EIC rows; the current model aggregates their EICs into
one sorted set.

The first response must contain at least 2,500 rows. Later responses are
blocked if row or unit count falls by more than 5%, or more than 5% of the
previous unit IDs disappear. This is a tripwire based on the 23 September 2026
full-source check (3,103 rows, 3,102 distinct IDs), not proof that a response
is complete. Investigate a blocked change at the source before changing the
threshold or accepting a new baseline.

Rejected rows enter `raw.endpoint_quarantine` with their source position and
errors. No row or success manifest from that response is published. The last
successful extract remains current, so a failed run cannot invalidate units in
the dbt snapshot.

## Storage and models

`raw.elexon_bm_units_extracts` stores one manifest for each committed complete
response, with UTC observation time, row count and distinct-unit count.
`raw.elexon_bm_units` stores every row at `(extract_id, source_index)` grain.
The manifest and its rows commit in one transaction. Both tables are
append-only.

`stg_elexon__bm_units` preserves every raw row and casts capacity strings to
numeric. `int_elexon__bm_units_current` selects the latest manifest and yields
one row per National Grid ID. The `snap_elexon__bm_units` dbt check snapshot
records observed changes and closes the version of a unit that leaves a
complete extract. Its validity timestamps are observation times, not business
effective dates. Null fuel stays unknown. A historical PN or B1610 unit is not
required to occur in today's registry.

## Recovery

The `gridskew_elexon_bmunits` DAG captures the registry, then loads the fuel
seed, tests the registry and records the snapshot, writing only to `dbt_dev`
and `dbt_dev_snapshots` and locking the raw registry tables against other
writers while dbt runs. If capture fails, check the source and the quarantine
and retry the DAG; if dbt fails after a good capture, fix the cause and retry
the dbt task, which stops if a newer extract has arrived. In that case run
from a fresh capture: the raw extracts keep every observation, but the
snapshot does not replay the ones it skipped.
