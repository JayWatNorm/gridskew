# Deploying to an external Airflow

This project does not run its own scheduler. The DAGs in `dags/` are written to
be deployed onto an Airflow instance that lives elsewhere and is shared with
other projects.

The homelab release coordinates this repository and the separate
`homelab-platform` checkout. Database provisioning remains an explicit step.

For why the pollers are written the way they are, see
[sources/ingestion-patterns.md](sources/ingestion-patterns.md). This page is
about getting them running somewhere.

## Two deployment mechanisms

A deployment moves DAG files and bind-mounted project code, and they do not
move the same way.

| Artefact | How it reaches Airflow | Effect of a `git pull` |
|---|---|---|
| `dags/*.py` | **copied** into the Airflow instance's DAG folder | none until copied again |
| `ingestion/` | **bind-mounted** from a checkout of this repository | takes effect on next task run |
| `dbt/` | **bind-mounted** from the same checkout | takes effect on the next dbt task run |

**DAGs are copies; project code is mounted.** This is the single most important
thing on the page, because the failure it causes is silent: pull a DAG change,
see it in the repository, and watch Airflow keep running the old one.

The asymmetry is deliberate rather than accidental. A shared scheduler needs one
flat namespace of DAG files it owns, whereas ingestion code is the project's own
package and should not be duplicated. The trade-off is that the DAG file exists
in two places and they can drift.

**A DAG file must end in `.py`.** Airflow's file processor discovers by
extension, so a file saved without one is invisible — no error, no import
failure, simply absent from the UI.

## Sequence

**1. Apply the DDL to every database the DAG will write to.**

The files in `sql/init/` are applied by hand, per database. There is no
migration tool. Confirm the result rather than assuming it:

```sql
\d raw.elexon_pn
```

A key that does not match the DDL will not fail at deploy time. It fails on the
first real load, part way through an insert, which is a worse place to find out.

Check ownership at the same time — a table owned by an administrative role
rather than the application role will fail on write, again only at run time:

```sql
SELECT tablename, tableowner FROM pg_tables WHERE schemaname = 'raw';
```

**2. Release the reviewed pair of repository revisions.**

Commit the deployment copy into `homelab-platform/dags/` alongside the GridSkew
change. The manual **Release GridSkew to Homelab** workflow in that repository
accepts the full 40-character commit SHA for each repository. Run the workflow
from `main`. Both selected commits must be on their respective remote `main`
histories. GridSkew's latest main-push CI run for the selected commit must pass.

Before adopting this workflow, disable the former automatic CD workflows in
both repositories, then merge the replacement/removal. Otherwise an old
push-triggered workflow can still deploy during the transition. Cancel any
old queued release. Configure the `production` GitHub environment with the
desired required reviewer and main-branch restriction. Merely naming an
environment does not enable approval protection. If GridSkew is private, set
`GRIDSKEW_RELEASE_TOKEN` in the platform repository with read access to
GridSkew Actions; an inaccessible CI result fails the release.

Pause all GridSkew DAGs, resolve queued runs and let active runs finish. Avoid
manual triggers throughout the release window. The workflow checks this state,
checks the two S4 DAG copies, verifies the raw S4 tables exist, and refuses
dirty host checkouts. GitHub concurrency and a host `flock` prevent overlapping
releases using this workflow. Other release processes must use the same lock.

The workflow checks out the exact commits in detached-HEAD mode, then checks
Airflow imports. On a command failure after switching code, it attempts to
restore both previous code revisions. The job summary records the old and new
pair. A killed runner may require manual recovery. No database rollback occurs.
Future releases use the workflow again; ordinary `git pull` is not the host
update procedure for a detached checkout.

This release updates mounted code and DAGs. It does not rebuild images, restart
services, apply Compose changes, provision database grants or run data models.
Infrastructure changes need their own reviewed procedure. The workflow has
local syntax checks; its first actual GitHub/homelab execution remains a
deployment acceptance check.

**3. Wait for the scheduler to pick it up.**

Two settings govern this:

| Setting | Default | What it controls |
|---|---|---|
| `dag_dir_list_interval` | 300s | how often the folder is rescanned for new files |
| `min_file_process_interval` | 30s | how often an individual file is re-parsed |

Files are parsed **independently, not as a batch**, and the processing queue is
not ordered alphabetically or by modification time. Two DAGs copied in the same
second routinely appear minutes apart and in arbitrary order. This is normal and
not a symptom of anything.

A file still missing after ten minutes is worth investigating:

```bash
airflow dags list-import-errors
```

**4. Leave the DAG paused and trigger one run manually.**

Verify the row count before allowing a schedule to run unattended. Expected
volumes are on each dataset's ingestion page.

**5. Unpause.**

## BM-unit registry S4 rollout

The S4 registry DAG and dbt models are prepared locally. Production remains
pending until the following steps succeed in order:

1. Confirm the existing `gridskew_prod` Airflow connection points to the
   production database. Create a separate Airflow Postgres connection named
   `gridskew_dbt`, using the same host, port and database (`gridskew_prod`),
   but the existing restricted dbt user that owns the `dbt_dev` objects.
   Confirm that user's identity and object ownership before the release; a
   different user may be unable to replace existing models or snapshots.
   The S4 DAG selects the homelab profile's `dev` target: seeds and models go
   to `gridskew_prod.dbt_dev`, and the
   snapshot goes to `gridskew_prod.dbt_dev_snapshots`. The existing `prod`
   profile and source-freshness DAG are unchanged. Inspect schema ownership
   before changing grants. Do not assume the dbt user name.
2. Apply [`007_elexon_bm_units.sql`](../sql/init/007_elexon_bm_units.sql)
   to the verified database as the production ingestion table owner, or make
   that role the owner after an administrator applies it. Confirm it can insert into both
   new raw tables and quarantine, and can hold SHARE locks on the new tables.
   Use an administrator to run
   [`001_bmu_snapshot_schema.sql`](../sql/deploy/001_bmu_snapshot_schema.sql)
   against `gridskew_prod` with
   `psql -v snapshot_schema=dbt_dev_snapshots -v dbt_role=<verified_dbt_role> -f ...`.
   It grants the dbt role read access to the new raw tables and write access
   to `dbt_dev` and `dbt_dev_snapshots`; it creates the snapshot schema if
   needed. Confirm the dbt role can read production raw data but cannot write
   to `raw`.
3. Release the GridSkew project checkout and the matching
   `gridskew_elexon_bmunits_dag.py` in `homelab-platform/dags`. Compare the
   two DAG files and check Airflow import errors. Keep the new DAG paused.
4. Trigger one observed run while the new DAG is paused. Confirm a manifest
   exists; its `row_count` matches the raw-row count; `unit_count` matches both distinct source IDs
   and `dim_bm_unit` rows; and the snapshot has one current version per ID.
   Check that the fuel-code seed and scoped relationship test passed. Verify
   the dbt objects are in `dbt_dev` and `dbt_dev_snapshots`, with no new S4
   objects in `public` or `public_snapshots`. Then unpause the daily DAG and
   resume the existing GridSkew DAG schedules.

The DAG fetches a complete response, then holds SHARE locks on both registry
raw tables while checking the expected manifest and running dbt seed/build/
test/snapshot. Inserts, updates and deletes wait until the guard transaction
ends; reads continue. In addition to SELECT, the guard role must own both tables
or hold table-level UPDATE, DELETE or TRUNCATE privilege to acquire SHARE locks.
Verify this explicitly; SELECT plus INSERT alone is insufficient. Prefer the
existing table-owning ingestion role over broadening an analysis-only role.
Acquisition waits at most 30 seconds. The dedicated guard connection
disables its idle-in-transaction timeout for the bounded task; do not terminate
that connection mid-run. A rejected or suspicious
response leaves the last good manifest current. If a model task fails after a
successful capture, fix the cause and retry that task while the captured
manifest is still latest. If a different extract became latest, rerun from a
fresh capture. Do not delete or recreate an existing production snapshot to
retry. New captures can supersede a failed observation; raw evidence remains,
but automatic historical replay is not implemented. Current views follow the
latest accepted raw manifest immediately, before dbt data tests finish.
Snapshot dates show when GridSkew observed a value, not when Elexon
first made it effective. The first snapshot has no pre-collection history.

The existing source freshness DAG remains a separate arrival monitor. It does
not run the S4 models or snapshot. S4 uses per-attempt temporary target/package
directories and persistent logs under `dbt_logs/bmunits/<attempt-uuid>` so the
two DAGs do not overwrite the same generated artifacts. Apply normal log
retention to that directory. A new S4 DAG is explicitly paused on creation;
existing DAG pause states are retained.

## How CI applies init SQL

CI starts an empty PostgreSQL 16 service. Its **Apply sql/init DDL and load
disposable CI fixtures** step runs `tests/adhoc/load_ci_fixtures.py`. That
script's `main()` opens a connection and calls `run_ddl(conn)`, which sorts and
executes every `sql/init/*.sql` file before committing and loading fixtures.
The subsequent dbt build therefore finds the raw tables and sample data.

This is a Python setup step, not a PostgreSQL container init-directory mount.
It does not execute `sql/deploy/001_bmu_snapshot_schema.sql`, and it is not a
production migration system. The fixture and lifecycle scripts require
`GRIDSKEW_DISPOSABLE_TEST=1`, a loopback host and database `gridskew_dev`;
connection fields use explicit `DBT_*` variables. CI's administrative role
does not prove that the production runtime grants are sufficient. Verify those
grants and the actual profile target during the first observed production run.

## Production dbt freshness

`gridskew__dbt_freshness_dag` runs at 20 and 50 minutes past each hour, after
the forecast collector's 5- and 35-minute runs. It executes:

```bash
dbt source freshness
```

The task reads the bind-mounted project from
`/opt/airflow/project/gridskew/dbt` and the deployment-owned profile from
`/opt/airflow/dbt_profiles`. Generated artifacts and logs use writable data
paths rather than the repository checkout:

```text
/opt/airflow/data/gridskew/dbt_target
/opt/airflow/data/gridskew/dbt_packages
/opt/airflow/data/gridskew/dbt_logs
```

Database credentials come from the `gridskew_prod` Airflow Connection. A dbt
freshness return code of 0 passes; every non-zero result (including return code
1 for warnings) fails the task via `result.check_returncode()`, ensuring stale
sources immediately alert in Airflow.

This scheduled command checks source arrival only. It does **not** load seeds,
build models or run their data tests. GitHub CI performs a complete
fixture-backed `dbt build` in a temporary database, while production model and
seed materialisation remains a separate release or future scheduled-build
step.

## Backfills

The PN, QPN and B1610 II DAGs use `catchup=True`, so **unpausing one starts a
backfill**. Their `start_date` is a fixed literal that defines how much history
to load. The B1610 SF DAG uses `catchup=False` and runs forwards only.

Runs execute one at a time under `max_active_runs=1`. For a daily-chunked Elexon
DAG that works out at roughly 1.4 requests per minute, and a year of history
takes about four hours.

**Pausing mid-backfill is safe.** The in-flight run finishes, queued runs stop,
and unpausing resumes where it left off. Each run is addressed to a specific
interval, so nothing is lost and nothing is repeated.

**An `elexon` pool with one slot now enforces this** — created 2026-08-25. Every
Elexon task carries `@task(pool="elexon")`, so only one runs at a time across all
four DAGs regardless of schedule. Carbon intensity is deliberately excluded:
different API, tiny payloads, no reason to queue behind an Elexon backfill.

```bash
docker compose exec airflow airflow pools list
```

The constraint it protects is **worker memory**, not the API. Each poller
materialises its whole response in Python before inserting — B1610 alone is
~450,000 rows — and several at once on a single `LocalExecutor` is a plausible
OOM. An OOM mid-backfill kills a run that then has to be cleared by hand.

**A task pointing at a pool that does not exist will not run**, so create the
pool before deploying a DAG that references it.

### `start_date` is a deployment decision

It defines the history window, and it is a literal on purpose.

Standing a DAG up on a fresh Airflow instance later re-backfills from that same
date, so the window grows with elapsed time. Change the literal deliberately if
that is not wanted.

**Never compute it from `datetime.now()`.** `start_date` is the anchor Airflow
uses to work out which intervals exist. A DAG module is re-parsed roughly every
thirty seconds, so a computed value shifts the schedule underneath itself and
runs get skipped or duplicated. The reasoning is in
[sources/elexon/011_pn_ingestion.md](sources/elexon/011_pn_ingestion.md).

## A UTC schedule is not a settlement day

`@daily` produces intervals running midnight to midnight **UTC**. The GB
settlement day starts at midnight **British local time**, which under BST is
23:00 UTC the previous day.

So for roughly half the year each run collects periods 3 to 48 of one settlement
day plus periods 1 and 2 of the next. Consecutive runs are contiguous in
absolute time, so nothing is lost — but **one DAG run is not one settlement
day**, and any downstream model that assumes otherwise is wrong for half the
year.

The earliest day of a backfill is a partial for the same reason: the periods
before the first window opens have no run to collect them.

## Reference implementation

The instance this project currently deploys to is a single-container Airflow
with a `LocalExecutor`, shared between several projects, defined in a separate
`homelab-platform` repository. Each project contributes DAG files into one
shared `dags/` folder and has its own repository bind-mounted for code.

Run `docker compose` commands from `~/homelab-platform` so Compose resolves the
short **service** names from the compose file rather than the auto-generated
container names:

```bash
cd ~/homelab-platform
docker compose exec airflow airflow dags list-import-errors
docker compose exec postgres-prod psql -U gridskew -d gridskew_prod
```

The Carbon forecast DAG runs at five and 35 minutes past each hour so it does
not poll exactly while NESO's half-hour boundary update may still be publishing.
Its 35-minute SLA expires five minutes after each scheduled run becomes due and
requires the shared deployment to have `core.check_slas=True`. The homelab
Compose file makes that setting explicit and the Airflow image includes the
`simplejson` dependency used by B1610 quarantine.

After deployment, confirm the effective setting, confirm no DAG import errors, observe
an on-time scheduled forecast run without a miss, and use a harmless controlled
scheduled test to prove a late run appears under **Browse → SLA Misses**. A
manual trigger does not exercise Airflow's SLA check.

`docker exec` works from anywhere but needs the full name —
`homelab-platform-airflow-1`, `homelab-platform-postgres-prod-1`.

Database credentials come from an Airflow Connection rather than the `.env` file
the pollers use when run directly, so nothing secret is copied anywhere. Each
project's environment variables on the shared container are namespaced by
project to avoid collision.

None of that is a requirement of this project. Any Airflow that can read the
`ingestion/` package and reach the database will run these DAGs.
