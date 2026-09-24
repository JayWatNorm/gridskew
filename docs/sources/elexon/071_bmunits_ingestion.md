# BM-unit registry ingestion

GridSkew polls `GET /reference/bmunits/all` once per day. The endpoint gives
the current registry, with no historical backfill or source update timestamp.
Collection starts an **observed** history; it cannot recover earlier changes.
The S4 code is checked in locally, but production collection starts only after
the deployment checks in [the deployment guide](../../deployment.md).

## Complete-extract gate

The poller requires a successful HTTP response and a non-empty list in which
every row passes the 22-field contract. Each row needs a nonblank National Grid
BM-unit ID. Populated capacity strings must use ordinary decimal/scientific
notation within PostgreSQL numeric limits. ASCII whitespace is trimmed;
whitespace-only strings become null in dbt staging. A repeated unit ID is accepted only when
its EICs are distinct and populated and every other source attribute agrees.
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
one row per National Grid ID. `dim_bm_unit` exposes that current state. The
`snap_elexon__bm_units` dbt check snapshot records observed changes and closes
versions for genuine disappearances. Its validity timestamps are observation
times, not business effective dates. Null fuel stays unknown. A historical PN
or B1610 unit is not required to occur in today's registry.

## Recovery

The single `gridskew_elexon_bmunits` Airflow DAG runs capture, current-model
build and tests, and snapshot in order, with one active run at a time. If
deployed, capture writes production raw tables; its dbt task reads the same
database through the separate `gridskew_dbt` connection and writes only to
`dbt_dev` and `dbt_dev_snapshots`. If
capture fails, inspect the source and quarantine evidence, then retry the DAG.
If dbt fails after a successful capture, repair the model or database issue
and retry the dbt task against that manifest. The task holds PostgreSQL SHARE
locks on both raw registry tables while it checks the expected manifest and
runs dbt seed/build/test/snapshot. Other database writers wait; readers can
continue. Lock acquisition times out after 30 seconds. A different latest
manifest stops the task. The guard connection must remain alive for the task;
do not terminate it during a run. Temporary dbt target/package directories are
unique per attempt; logs remain under `dbt_logs/bmunits/<attempt-uuid>`.

If a newer capture superseded a failed task, run from a fresh capture. Retained
raw extracts preserve the evidence, but this snapshot does not automatically
replay skipped observations. A newly accepted raw capture updates the current
views before dbt validation completes; there is no separate approved-data pointer.
Do not run a separate scheduled snapshot against an unverified extract.
