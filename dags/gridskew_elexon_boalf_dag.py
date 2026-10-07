"""Load Elexon bid-offer acceptance levels each day, including historical runs.

Each run loads the ramp points that start in its own UTC day. To repeat a
day, clear its scheduled task instance; do not trigger a manual run.
"""

import sys
from datetime import datetime, timedelta, timezone

from airflow.decorators import dag, task
from airflow.timetables.interval import CronDataIntervalTimetable

# Namespaced per project, matching the bind-mount declared in the Airflow
# compose file. Deliberately not a global PYTHONPATH:
PROJECT_PATH = "/opt/airflow/project/gridskew"


@dag(
    dag_id="gridskew_elexon_boalf",
    # Not "@daily": this timetable keeps a full-day data interval on Airflow 3.
    schedule=CronDataIntervalTimetable("0 0 * * *", timezone="UTC"),
    start_date=datetime(2025, 8, 22, tzinfo=timezone.utc),
    catchup=True,
    max_active_runs=1,
    is_paused_upon_creation=True,
    default_args={
        "retries": 3,
        "retry_delay": timedelta(minutes=5),
        "execution_timeout": timedelta(minutes=20),
    },
    tags=["gridskew", "elexon"],
)
def gridskew_elexon_boalf():

    @task(pool="elexon")
    def poll_boalf(data_interval_start=None, data_interval_end=None):
        from airflow.providers.postgres.hooks.postgres import PostgresHook

        if data_interval_start is None or data_interval_end is None:
            # Airflow 3 manual runs have no data interval.
            raise RuntimeError(
                "No data interval: clear a scheduled task instance to repeat a capture"
            )

        sys.path.insert(0, PROJECT_PATH)
        from ingestion.elexon.boalf_poller import run

        # Credentials come from the Airflow Connection.
        conn = PostgresHook(postgres_conn_id="gridskew_prod").get_conn()
        try:
            run(conn, data_interval_start, data_interval_end)
        finally:
            conn.close()

    poll_boalf()


gridskew_elexon_boalf()
