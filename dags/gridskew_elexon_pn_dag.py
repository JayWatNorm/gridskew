"""Load Elexon Physical Notifications each day, including historical runs."""

import sys
from datetime import datetime, timedelta, timezone

from airflow.sdk import dag, task
from airflow.timetables.interval import CronDataIntervalTimetable

# The platform mounts the GridSkew checkout here.
PROJECT_PATH = "/opt/airflow/project/gridskew"


@dag(
    dag_id="gridskew_elexon_pn",
    # Not "@daily": this timetable keeps a full-day data interval on Airflow 3.
    schedule=CronDataIntervalTimetable("0 0 * * *", timezone="UTC"),
    start_date=datetime(2025, 8, 22, tzinfo=timezone.utc),
    catchup=True,
    # A date that keeps retrying does not hold up the dates after it; the one
    # elexon pool slot still keeps the requests one at a time.
    max_active_runs=3,
    # Retry for about four days, backing off to 8 hours between tries, so an
    # Elexon outage recovers without anyone clearing a task.
    default_args={
        "retries": 16,
        "retry_delay": timedelta(minutes=15),
        "retry_exponential_backoff": True,
        "max_retry_delay": timedelta(hours=8),
        "execution_timeout": timedelta(minutes=20),
    },
    tags=["gridskew", "elexon"],
)
def gridskew_elexon_pn():

    @task(pool="elexon")
    def poll_pn(data_interval_start=None, data_interval_end=None):
        from airflow.sdk import BaseHook
        from airflow.sdk.exceptions import AirflowFailException

        if data_interval_start is None or data_interval_end is None:
            # Airflow 3 manual runs have no data interval.
            raise AirflowFailException(
                "No data interval: clear a scheduled task instance to repeat a capture, "
                "or use `airflow backfill create` for past days"
            )

        sys.path.insert(0, PROJECT_PATH)

        from ingestion.database import connect
        from ingestion.elexon.pn_poller import run
        from ingestion.routing import ContractRejected

        conn = connect(BaseHook.get_connection("gridskew_prod"))
        try:
            run(conn, data_interval_start, data_interval_end)
        except ContractRejected as rejected:
            raise AirflowFailException(str(rejected)) from rejected
        finally:
            conn.close()

    poll_pn()


gridskew_elexon_pn()
