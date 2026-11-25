# Deployment

GridSkew runs on a shared homelab Airflow deployment managed in the
`homelab-platform` repository, which is private. Its GitHub Actions CD
workflow, **Release GridSkew to Homelab**, is dispatched manually from that
repository.

The workflow takes exact commits from GridSkew and homelab-platform. It
requires a passing GridSkew CI run, pauses and drains the affected GridSkew
DAGs, updates both host checkouts, verifies Airflow, and then restores or
holds schedules according to the selected activation mode. GridSkew's `dags/`,
`ingestion/` and `dbt/` directories are mounted into Airflow from the
GridSkew checkout, so a DAG change needs only a GridSkew commit.

A new database needs these steps before the nightly dbt job can run. The
`gridskew_dbt` role is created first. The database owner then runs
`sql/provision/001_bmu_snapshot_schema.sql` with
`-v snapshot_schema=dbt_dev_snapshots -v dbt_role=gridskew_dbt`. It creates
the `dbt_dev` and `dbt_dev_snapshots` schemas, lets the role use and create
in them and use `raw`, and grants it `SELECT` on every `raw` table
declared as a dbt source. Then the project is built once with
`dbt build`, as CI does (from a workstation, with `--target prod`). The
nightly job's first step tests the staged
B1610 data and its run hook writes to `dbt_dev.dbt_node_results`, so on an
empty database it fails before it creates anything. The production models
live in schema `dbt_dev` for historical reasons.

The dbt jobs on the host read the platform repository's profile, mounted at
`/opt/airflow/dbt_profiles`, and pass their connection values to dbt as
environment variables. The profile in this repository, `dbt_profiles/`, is
for a workstation and for CI: its default target, `dev`, is the local
disposable database (`localhost`, `gridskew_dev`, both fixed), and its
`prod` target is selected only by `--target prod` or `DBT_TARGET=prod`. A
GridSkew release changes nothing about which profile the host uses.

The ingestion connection (`gridskew_prod` in Airflow) logs in as the loader
role `gridskew_loader`, which can insert into the `raw` tables and read
them, and nothing more, so the database refuses an update or delete of a
source row by a loader; the owner, `gridskew`, keeps the migrations and the
hand steps such as marking a quarantine row resolved. An administrator
creates the role with
`sql/provision/002_loader_role.sql`, which refuses a role that already
exists, grants it with `004_loader_grants.sql` and proves its effective
rights with `003_verify_loader_role.sql`, each with
`-v loader_role=<the role>`; the login and password are set by hand after
creation, and the verification is run with `-v expect_login=true` and must
pass before the connection is switched to the role. A migration that adds a
source table ships with its grant lines in `004`, which is re-run before
that table's loader is released; the grants are idempotent, so a re-run
changes nothing else. The BM-unit capture locks the registry tables through
`raw.lock_bm_unit_registry()`, which the owner defines, so the role needs no
update right.

This page is the public account of the release process. The host SQL, the
Airflow pool prerequisites, the workflow inputs, the observed rollout checks
and the recovery steps are in the `homelab-platform` runbook,
`docs/gridskew-release.md`, which is not public.

The daily Elexon DAGs (B1610 II, SF and RF, PN, QPN, BOALF) fetch the day
set by the run's data interval; QAS captures three earlier dates per run
(its [ingestion page](sources/elexon/091_qas_ingestion.md) explains). To repeat a capture, clear the scheduled
task instance; to fetch past days, use `airflow backfill create --dag-id <id>
--from-date <first day> --to-date <last day>`. A manual trigger has no data
interval on Airflow 3, and the DAGs refuse it.
