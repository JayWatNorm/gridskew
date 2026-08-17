"""gridskew's carbon intensity forecast archive DAG.

Polls the NESO Carbon Intensity API every 30 minutes and appends every
revision to raw.carbon_intensity_forecast in gridskew_prod.

This is the source copy. A namespaced duplicate lives in
homelab-platform/dags/ and is what the shared Airflow instance actually
loads -- keep the two in sync by hand. The ingestion package itself is NOT
duplicated: homelab-platform's Airflow bind-mounts gridskew/ingestion in
from the sibling repo, so there is exactly one copy of the real code.

Scheduled runs always target PROD (homelab-infrastructure.md). Dev
iteration happens by running forecast_poller.py locally against
gridskew_dev, not by pointing this DAG somewhere else.
"""

import sys
from datetime import datetime, timedelta, timezone

from airflow.decorators import dag, task

# Namespaced per project, matching the bind-mount in
# homelab-platform/docker-compose.yml. Deliberately not a global PYTHONPATH:
# a second project's own `ingestion` package must never win this import.
PROJECT_PATH = "/opt/airflow/project/gridskew"


@dag(
    dag_id="gridskew_carbon_intensity",
    schedule="*/30 * * * *",
    start_date=datetime(2026, 8, 17, tzinfo=timezone.utc),
    catchup=False,
    max_active_runs=1,
    default_args={
        "retries": 3,
        "retry_delay": timedelta(minutes=5),
    },
    tags=["gridskew", "carbon-intensity"],
)
def gridskew_carbon_intensity_dag():

    @task
    def poll_forecast():
        # Imports are deferred into the task body on purpose. The scheduler
        # re-parses this file constantly; anything imported at module level
        # runs every time, and an ImportError there removes the whole DAG
        # from the UI rather than failing one task with a readable log.
        from airflow.providers.postgres.hooks.postgres import PostgresHook

        sys.path.insert(0, PROJECT_PATH)
        from ingestion.carbon_intensity.forecast_poller import run

        # Credentials come from the Airflow Connection, not from env vars on
        # the shared container -- decision #003, and the fix flagged in
        # homelab-platform/docker-compose.yml for when a second project
        # arrives. gridskew is that second project.
        conn = PostgresHook(postgres_conn_id="gridskew_prod").get_conn()
        try:
            run(conn)
        finally:
            conn.close()

    poll_forecast()


gridskew_carbon_intensity_dag()
