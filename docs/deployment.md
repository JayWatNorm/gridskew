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
empty database it fails before it creates anything.

This page is the CD overview. Host SQL, Airflow pool prerequisites, workflow
inputs, observed rollout checks and recovery are documented in
`homelab-platform/docs/gridskew-release.md` in the platform checkout.

The daily Elexon DAGs (B1610 II, SF and RF, PN, QPN, BOALF) fetch the day
set by the run's data interval; QAS captures three earlier dates per run
(its [ingestion page](sources/elexon/091_qas_ingestion.md) explains). To repeat a capture, clear the scheduled
task instance; to fetch past days, use `airflow backfill create --dag-id <id>
--from-date <first day> --to-date <last day>`. A manual trigger has no data
interval on Airflow 3, and the DAGs refuse it.
