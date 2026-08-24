"""gridskew's elexon B1610 Type II dag, retrieves actuals, theres a 5 day wait before the availability
but on testing ive found its 7, 14 days has been set as the lag to account for unusual  holiday peroids
II is the first actual it is followed by the first set of revision in the SF Type"""

import sys
from datetime import datetime, timedelta, timezone

from airflow.decorators import dag, task

# Namespaced per project, matching the bind-mount declared in the Airflow
# compose file. Deliberately not a global PYTHONPATH:
PROJECT_PATH = "/opt/airflow/project/gridskew"


@dag(
    dag_id="gridskew_elexon_b1610_II",
    schedule="@daily",
    start_date=datetime(2025, 9, 5, tzinfo=timezone.utc),
    catchup=True,
    max_active_runs=1,
    default_args={
        "retries": 3,
        "retry_delay": timedelta(minutes=5),
        "execution_timeout": timedelta(minutes=45),
    },
    tags=["gridskew", "elexon"],
)
def gridskew_elexon_b1610_II():
    
    @task(pool="elexon")  
    def poll_b1610_II(data_interval_start, data_interval_end):
        from airflow.providers.postgres.hooks.postgres import PostgresHook

        sys.path.insert(0, PROJECT_PATH)
        from ingestion.elexon.b1610_poller import run

        LAG = timedelta(days=14)

        # Credentials come from the Airflow Connection.
        conn = PostgresHook(postgres_conn_id="gridskew_prod").get_conn()
        try:
            run(conn, data_interval_start - LAG, data_interval_end - LAG)
        finally:
            conn.close()

    poll_b1610_II()


gridskew_elexon_b1610_II()
