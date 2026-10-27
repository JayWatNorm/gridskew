"""Load the B1610 Interim Information settlement position each day.

II is published after five working days and stays in force until SF replaces
it, about three weeks after the settlement date. The 14-day offset leaves
about a week of that window for retries.

Each run captures one British settlement date and asks for II by name. The
task fails when the stored date lacks a settlement period or the source
returns another run, and retries for about four days, so an Elexon outage or a
late publication recovers without anyone clearing it. Do not trigger a manual
run for an old date.
"""

import sys
from datetime import datetime, timedelta, timezone

from airflow.sdk import dag, task
from airflow.timetables.interval import CronDataIntervalTimetable

# The platform mounts the GridSkew checkout here.
PROJECT_PATH = "/opt/airflow/project/gridskew"

RUN_TYPE = "II"
LAG = timedelta(days=14)


def settlement_date_to_capture(data_interval_start):
    """Return the British settlement date that one daily run captures."""

    return (data_interval_start - LAG).date()


@dag(
    dag_id="gridskew_elexon_b1610_II",
    # Not "@daily": this timetable keeps a full-day data interval on Airflow 3.
    schedule=CronDataIntervalTimetable("0 0 * * *", timezone="UTC"),
    # The B1610 history start, 2025-08-22, plus LAG: the first run captures it.
    start_date=datetime(2025, 9, 5, tzinfo=timezone.utc),
    catchup=True,
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
def gridskew_elexon_b1610_II():

    @task(pool="elexon")
    def poll_b1610_II(data_interval_start=None):
        from airflow.sdk.exceptions import AirflowFailException

        if data_interval_start is None:
            # Airflow 3 manual runs have no data interval.
            raise AirflowFailException(
                "No data interval: clear a scheduled task instance to repeat a capture, "
                "or use `airflow backfill create` for past days"
            )
        settlement_date = settlement_date_to_capture(data_interval_start)

        sys.path.insert(0, PROJECT_PATH)
        from ingestion.dag_tasks import capture_unless_replaced
        from ingestion.elexon.b1610_poller import (
            capture_settlement_date,
            replaced_by_a_later_run,
        )

        capture_unless_replaced(
            settlement_date,
            RUN_TYPE,
            replaced_by_a_later_run=replaced_by_a_later_run,
            capture_settlement_date=capture_settlement_date,
        )

    poll_b1610_II()


gridskew_elexon_b1610_II()
