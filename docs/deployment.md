# Deployment

GridSkew runs on a shared homelab Airflow deployment managed in the
`homelab-platform` repository. Its GitHub Actions CD workflow, **Release
GridSkew to Homelab**, is dispatched manually from that repository.

The workflow takes exact commits from GridSkew and homelab-platform. It
requires a passing GridSkew CI run, checks the matching DAG copies, pauses
and drains GridSkew runs, updates both host checkouts, verifies Airflow, and
then restores or holds schedules according to the selected release mode.
GridSkew's `ingestion/` and `dbt/` code is mounted into Airflow; deployed
DAG files live in `homelab-platform/dags/`.

Host preparation, workflow inputs, verification, and recovery are documented
in the `homelab-platform` repository.
