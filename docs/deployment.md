# Deployment

GridSkew runs on a shared homelab Airflow deployment managed in the
`homelab-platform` repository. Its GitHub Actions CD workflow, **Release
GridSkew to Homelab**, is dispatched manually from that repository.

The workflow takes exact commits from GridSkew and homelab-platform. It
requires a passing GridSkew CI run, pauses and drains the affected GridSkew
DAGs, updates both host checkouts, verifies Airflow, and then restores or
holds schedules according to the selected release mode. GridSkew's `dags/`,
`ingestion/` and `dbt/` directories are mounted into Airflow from the
GridSkew checkout, so a DAG change needs only a GridSkew commit.

This page is the CD overview. Host SQL, Airflow pool prerequisites, workflow
inputs, observed rollout checks and recovery are documented in
`homelab-platform/docs/gridskew-release.md` in the platform checkout.
