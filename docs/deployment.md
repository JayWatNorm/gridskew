# Deployment

GridSkew runs on a shared homelab Airflow deployment managed in the
`homelab-platform` repository. Its GitHub Actions CD workflow, **Release
GridSkew to Homelab**, is dispatched manually from that repository.

The workflow takes exact commits from GridSkew and homelab-platform. It
requires a passing GridSkew CI run, pauses and drains the affected GridSkew
DAGs, updates both host checkouts, verifies Airflow, and then restores or
holds schedules according to the selected activation mode. GridSkew's `dags/`,
`ingestion/` and `dbt/` directories are mounted into Airflow from the
GridSkew checkout, so a DAG change needs only a GridSkew commit.

A new database needs two steps before the nightly dbt job can run. The
database owner runs `sql/provision/001_bmu_snapshot_schema.sql`, which
creates the `dbt_dev` and `dbt_dev_snapshots` schemas and grants the
`gridskew_dbt` role what it needs, and the project is built once with
`dbt build`, as CI does. The nightly job's first step tests the staged
B1610 data and its run hook writes to `dbt_dev.dbt_node_results`, so on an
empty database it fails before it creates anything. The production models
live in schema `dbt_dev` for historical reasons.

The ingestion connection (`gridskew_prod` in Airflow) uses a loader role
that can insert into the `raw` tables and read them, and nothing more; the
owner, `gridskew`, keeps the hand steps such as marking a quarantine row
resolved. An administrator creates the role with
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

This page is the CD overview. Host SQL, Airflow pool prerequisites, workflow
inputs, observed rollout checks and recovery are documented in
`homelab-platform/docs/gridskew-release.md` in the platform checkout.

The daily Elexon DAGs (B1610 II, SF and RF, PN, QPN, BOALF) fetch the day
set by the run's data interval; QAS captures three earlier dates per run
(its [ingestion page](sources/elexon/091_qas_ingestion.md) explains). To repeat a capture, clear the scheduled
task instance; to fetch past days, use `airflow backfill create --dag-id <id>
--from-date <first day> --to-date <last day>`. A manual trigger has no data
interval on Airflow 3, and the DAGs refuse it.
