"""Capture QAS (ABSVD) for the settlement dates 14, 35 and 450 days back.

The three lags match the B1610 captures, so each metered run sits beside the
ABSVD known at the same age. NESO may resubmit a value before Final
Reconciliation, and the source carries no version: a later capture is the
only way to see a change, so every capture is kept.

Each task captures one British settlement date (two requests on a 50-period
date). It fails when
the response is empty (every target is past NESO's publication window) or is
not one whole date, and retries for about four days. A target before the first
QAS date GridSkew holds is skipped. After an outage the scheduler creates the
runs it missed. The dates before the first run came from the one-off backfill
(ingestion/elexon/qas_backfill.py). Do not trigger a manual run for an old
date.
"""

import sys
from datetime import date, datetime, timedelta, timezone

from airflow.sdk import dag, task
from airflow.timetables.interval import CronDataIntervalTimetable

# The platform mounts the GridSkew checkout here.
PROJECT_PATH = "/opt/airflow/project/gridskew"

# The first settlement date of the shortfall table; the backfill starts here.
FIRST_SETTLEMENT_DATE = date(2025, 8, 22)


def settlement_date_to_capture(data_interval_start, lag_days):
    """Return the British settlement date one daily run captures at a lag."""

    return (data_interval_start - timedelta(days=lag_days)).date()


def capture_at_lag(data_interval_start, lag_days):
    """Capture the settlement date lag_days before the run's data interval."""

    from airflow.sdk import BaseHook
    from airflow.sdk.exceptions import AirflowFailException, AirflowSkipException

    if data_interval_start is None:
        # Airflow 3 manual runs have no data interval.
        raise AirflowFailException(
            "No data interval: clear a scheduled task instance to repeat a capture, "
            "or use `airflow backfill create` for past days"
        )
    settlement_date = settlement_date_to_capture(data_interval_start, lag_days)
    if settlement_date < FIRST_SETTLEMENT_DATE:
        raise AirflowSkipException(
            f"{settlement_date} is before {FIRST_SETTLEMENT_DATE}, the first "
            "settlement date GridSkew holds"
        )

    sys.path.insert(0, PROJECT_PATH)
    from ingestion.database import connect
    from ingestion.elexon.qas_poller import run
    from ingestion.routing import ContractRejected

    conn = connect(BaseHook.get_connection("gridskew_prod"))
    try:
        run(conn, settlement_date)
    except ContractRejected as rejected:
        # A retry fetches the same rows.
        raise AirflowFailException(str(rejected)) from rejected
    finally:
        conn.close()


@dag(
    dag_id="gridskew_elexon_qas",
    # Not "@daily": this timetable keeps a full-day data interval on Airflow 3.
    schedule=CronDataIntervalTimetable("0 0 * * *", timezone="UTC"),
    # The first 14-day capture is 6 October 2026, the day after the backfill's
    # last date (qas_backfill.BACKFILL_LAST_DATE).
    start_date=datetime(2026, 10, 20, tzinfo=timezone.utc),
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
def gridskew_elexon_qas():

    @task(pool="elexon")
    def capture_14_days(data_interval_start=None):
        capture_at_lag(data_interval_start, 14)

    @task(pool="elexon")
    def capture_35_days(data_interval_start=None):
        capture_at_lag(data_interval_start, 35)

    @task(pool="elexon")
    def capture_450_days(data_interval_start=None):
        capture_at_lag(data_interval_start, 450)

    capture_14_days()
    capture_35_days()
    capture_450_days()


gridskew_elexon_qas()
