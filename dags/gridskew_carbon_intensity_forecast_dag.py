"""Archive each half-hourly NESO Carbon Intensity forecast revision."""

import sys
from datetime import datetime, timedelta, timezone

from airflow.decorators import dag, task

# The platform mounts the GridSkew checkout here.
PROJECT_PATH = "/opt/airflow/project/gridskew"


@dag(
    dag_id="gridskew_carbon_intensity",
    schedule="5,35 * * * *",
    start_date=datetime(2026, 8, 17, tzinfo=timezone.utc),
    catchup=False,
    max_active_runs=1,
    # Two-minute limits: a hung try cannot run into the next half-hourly
    # capture, provided the scheduler starts each try promptly.
    default_args={
        "retries": 3,
        "retry_delay": timedelta(minutes=2),
        "execution_timeout": timedelta(minutes=2),
    },
    tags=["gridskew", "carbon-intensity"],
)
def gridskew_carbon_intensity_dag():

    # The five-minute schedule offset allows NESO's boundary update to finish.
    # Airflow 2.10 evaluates the SLA from the scheduled logical date, so 35
    # minutes still allows five minutes for completion after the run is due.
    @task(sla=timedelta(minutes=35))
    def poll_forecast():
        from airflow.hooks.base import BaseHook

        sys.path.insert(0, PROJECT_PATH)
        from ingestion.carbon_intensity.forecast_poller import run
        from ingestion.database import connect

        conn = connect(BaseHook.get_connection("gridskew_prod"))
        try:
            run(conn)
        finally:
            conn.close()

    poll_forecast()


gridskew_carbon_intensity_dag()
