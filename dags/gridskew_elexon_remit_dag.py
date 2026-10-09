"""Load Elexon REMIT messages each hour.

The poller takes its window from the table: each run loads everything
published since the newest stored message. The first run on an empty table
loads the whole history, a day at a time.
"""

import sys
from datetime import datetime, timedelta, timezone

from airflow.decorators import dag, task

# Namespaced per project, matching the bind-mount declared in the Airflow
# compose file. Deliberately not a global PYTHONPATH:
PROJECT_PATH = "/opt/airflow/project/gridskew"


@dag(
    dag_id="gridskew_elexon_remit",
    schedule="15 * * * *",
    start_date=datetime(2026, 10, 8, tzinfo=timezone.utc),
    catchup=False,
    max_active_runs=1,
    is_paused_upon_creation=True,
    default_args={
        "retries": 2,
        "retry_delay": timedelta(minutes=10),
        "execution_timeout": timedelta(minutes=45),
    },
    tags=["gridskew", "elexon"],
)
def gridskew_elexon_remit():

    @task(pool="elexon")
    def poll_remit():
        from airflow.providers.postgres.hooks.postgres import PostgresHook

        sys.path.insert(0, PROJECT_PATH)
        from ingestion.elexon.remit_poller import run

        conn = PostgresHook(postgres_conn_id="gridskew_prod").get_conn()
        try:
            run(conn)
        finally:
            conn.close()

    poll_remit()


gridskew_elexon_remit()
