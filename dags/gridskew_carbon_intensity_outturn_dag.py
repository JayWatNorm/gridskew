"""gridskew's carbon intensity forecast archive DAG.
Polls the NESO Carbon Intensity API every 30 minutes and appends every
revision to raw.carbon_intensity_forecast in gridskew_prod.
"""

import sys
from datetime import datetime, timedelta, timezone

from airflow.decorators import dag, task

# Namespaced per project, matching the bind-mount declared in the Airflow
# compose file. Deliberately not a global PYTHONPATH:
PROJECT_PATH = "/opt/airflow/project/gridskew"


@dag(
    dag_id="gridskew_carbon_intensity_outturn",
    schedule="0 6 * * *",
    start_date=datetime(2026, 8, 17, tzinfo=timezone.utc),
    catchup=False,
    max_active_runs=1,
    default_args={
        "retries": 3,
        "retry_delay": timedelta(minutes=5),
        "execution_timeout": timedelta(minutes=15),
    },
    tags=["gridskew", "carbon-intensity"],
)
def gridskew_carbon_intensity_outturn():

    @task
    def poll_outturn():
        from airflow.providers.postgres.hooks.postgres import PostgresHook

        sys.path.insert(0, PROJECT_PATH)
        from ingestion.carbon_intensity.outturn_poller import run

        # Credentials come from the Airflow Connection.
        conn = PostgresHook(postgres_conn_id="gridskew_prod").get_conn()
        try:
            run(conn)
        finally:
            conn.close()

    poll_outturn()


gridskew_carbon_intensity_outturn()
