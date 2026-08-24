"""gridskew's elexon pns dag, retrieves commitments for the past day with catchup."""

import sys
from datetime import datetime, timedelta, timezone

from airflow.decorators import dag, task

# Namespaced per project, matching the bind-mount declared in the Airflow
# compose file. Deliberately not a global PYTHONPATH:
PROJECT_PATH = "/opt/airflow/project/gridskew"


@dag(
    dag_id="gridskew_elexon_pn",
    schedule="@daily",
    start_date=datetime(2025, 8, 22, tzinfo=timezone.utc),
    catchup=True,
    max_active_runs=1,
    default_args={
        "retries": 3,
        "retry_delay": timedelta(minutes=5),
        "execution_timeout": timedelta(minutes=20),
    },
    tags=["gridskew", "elexon"],
)
def gridskew_elexon_pn():

    @task(pool="elexon")  
    def poll_pn(data_interval_start=None, data_interval_end=None):
        from airflow.providers.postgres.hooks.postgres import PostgresHook

        sys.path.insert(0, PROJECT_PATH)
        from ingestion.elexon.pn_poller import run

        # Credentials come from the Airflow Connection.
        conn = PostgresHook(postgres_conn_id="gridskew_prod").get_conn()
        try:
            run(conn, data_interval_start, data_interval_end)
        finally:
            conn.close()

    poll_pn()


gridskew_elexon_pn()
