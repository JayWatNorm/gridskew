"""Load carbon-intensity outturn and final-revision forecasts each day.

Rows are appended to raw.carbon_intensity_outturn in gridskew_prod.
"""

import sys
from datetime import datetime, timedelta, timezone

from airflow.decorators import dag, task

# The platform mounts the GridSkew checkout here.
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
        from airflow.hooks.base import BaseHook

        sys.path.insert(0, PROJECT_PATH)
        from ingestion.carbon_intensity.outturn_poller import run
        from ingestion.database import connect

        conn = connect(BaseHook.get_connection("gridskew_prod"))
        try:
            run(conn)
        finally:
            conn.close()

    poll_outturn()


gridskew_carbon_intensity_outturn()
