"""Load the B1610 Final Reconciliation position at a 450-day offset.

RF stays in force once published until a Dispute Final run (DF, about 28
months) replaces it, so a late capture loses nothing: 450 days is after RF
is published (about 14 months after settlement dates before 1 October 2026,
seven months after later ones) and well before DF. A date DF has already
replaced fails at once.

Each run captures one British settlement date and asks for RF by name. The
task fails when the stored date lacks a settlement period or the source
returns another run, and retries for about four days; a date that still fails
can be cleared at any later time. Do not trigger a manual run for an old date.
"""

import sys
from datetime import datetime, timedelta, timezone

from airflow.sdk import dag, task
from airflow.timetables.interval import CronDataIntervalTimetable

# The platform mounts the GridSkew checkout here.
PROJECT_PATH = "/opt/airflow/project/gridskew"

RUN_TYPE = "RF"
LAG = timedelta(days=450)


def settlement_date_to_capture(data_interval_start):
    """Return the British settlement date that one daily run captures."""

    return (data_interval_start - LAG).date()


@dag(
    dag_id="gridskew_elexon_b1610_RF",
    # Not "@daily": this timetable keeps a full-day data interval on Airflow 3.
    schedule=CronDataIntervalTimetable("0 0 * * *", timezone="UTC"),
    # The first run captures 2025-08-22, where the B1610 history starts.
    start_date=datetime(2026, 11, 15, tzinfo=timezone.utc),
    catchup=True,
    is_paused_upon_creation=True,
    # A date that keeps retrying does not hold up the dates after it; the one
    # elexon pool slot still keeps the requests one at a time.
    max_active_runs=3,
    # Retry for about four days, backing off to 8 hours between tries. Each try
    # is short, so a hung poll frees the elexon pool slot quickly.
    default_args={
        "retries": 16,
        "retry_delay": timedelta(minutes=15),
        "retry_exponential_backoff": True,
        "max_retry_delay": timedelta(hours=8),
        "execution_timeout": timedelta(minutes=10),
    },
    tags=["gridskew", "elexon"],
)
def gridskew_elexon_b1610_RF():

    @task(pool="elexon")
    def poll_b1610_RF(data_interval_start=None):
        from airflow.sdk import BaseHook
        from airflow.sdk.exceptions import AirflowFailException

        if data_interval_start is None:
            # Airflow 3 manual runs have no data interval.
            raise AirflowFailException(
                "No data interval: clear a scheduled task instance to repeat a capture, "
                "or use `airflow backfill create` for past days"
            )
        settlement_date = settlement_date_to_capture(data_interval_start)

        sys.path.insert(0, PROJECT_PATH)
        from ingestion.database import connect
        from ingestion.elexon.b1610_poller import (
            capture_settlement_date,
            replaced_by_a_later_run,
        )
        from ingestion.routing import ContractRejected

        replacing_run = replaced_by_a_later_run(settlement_date, RUN_TYPE)
        if replacing_run is not None:
            # Gone for good: fail now instead of retrying for days.
            raise AirflowFailException(
                f"{RUN_TYPE} for {settlement_date} has been replaced by "
                f"{replacing_run} and can no longer be captured"
            )

        conn = connect(BaseHook.get_connection("gridskew_prod"))
        try:
            capture_settlement_date(conn, settlement_date, RUN_TYPE)
        except ContractRejected as rejected:
            raise AirflowFailException(str(rejected)) from rejected
        finally:
            conn.close()

    poll_b1610_RF()


gridskew_elexon_b1610_RF()
