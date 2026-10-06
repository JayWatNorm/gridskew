"""Capture bounded B1610 settlement-run cohorts on fixed dates.

Each event captures one British settlement date for one settlement run. The
mapping below is the only schedule; the task refuses any other date. Clear a
task instance to repeat a capture; do not trigger a manual run for an old date.
"""

import sys
from datetime import date, datetime, timedelta, timezone

import pendulum
from airflow.decorators import dag, task
from airflow.timetables.events import EventsTimetable

# Namespaced per project, matching the bind-mount declared in the Airflow
# compose file. Deliberately not a global PYTHONPATH:
PROJECT_PATH = "/opt/airflow/project/gridskew"

CAPTURE_HOUR_UTC = 6

# Capture date (UTC) -> (settlement run, British settlement date).
# R1: +56 days (plan of record, 2026-09-11 decision).
COHORT_CAPTURES = {
    date(2026, 10, 5): ("R1", date(2026, 8, 10)),
    date(2026, 10, 6): ("R1", date(2026, 8, 11)),
    date(2026, 10, 7): ("R1", date(2026, 8, 12)),
    date(2026, 10, 8): ("R1", date(2026, 8, 13)),
    date(2026, 10, 9): ("R1", date(2026, 8, 14)),
    date(2026, 10, 10): ("R1", date(2026, 8, 15)),
    date(2026, 10, 11): ("R1", date(2026, 8, 16)),
}

COHORT_EVENTS = [
    pendulum.datetime(day.year, day.month, day.day, CAPTURE_HOUR_UTC, tz="UTC")
    for day in sorted(COHORT_CAPTURES)
]


def cohort_capture(capture_date):
    """Return the run type and settlement date for one scheduled capture."""

    capture = COHORT_CAPTURES.get(capture_date)
    if capture is None:
        raise RuntimeError(f"No B1610 cohort capture is defined for {capture_date}")
    return capture


@dag(
    dag_id="gridskew_elexon_b1610_cohort",
    schedule=EventsTimetable(
        event_dates=COHORT_EVENTS,
        restrict_to_events=True,
        description="Bounded B1610 cohort captures",
    ),
    start_date=datetime(2026, 10, 1, tzinfo=timezone.utc),
    catchup=True,
    max_active_runs=1,
    is_paused_upon_creation=True,
    default_args={
        "retries": 3,
        "retry_delay": timedelta(minutes=10),
        "execution_timeout": timedelta(minutes=45),
    },
    tags=["gridskew", "elexon", "cohort"],
)
def gridskew_elexon_b1610_cohort():

    @task(pool="elexon")
    def capture_cohort(logical_date=None):
        from airflow.providers.postgres.hooks.postgres import PostgresHook

        if logical_date is None:
            # Airflow 3 manual runs have no logical date (HL-10 §2).
            raise RuntimeError(
                "No logical date: clear a scheduled task instance to repeat a capture"
            )
        run_type, settlement_date = cohort_capture(logical_date.date())

        sys.path.insert(0, PROJECT_PATH)
        from ingestion.elexon.b1610_poller import capture_settlement_date

        # Credentials come from the Airflow Connection.
        conn = PostgresHook(postgres_conn_id="gridskew_prod").get_conn()
        try:
            capture_settlement_date(conn, settlement_date, run_type)
        finally:
            conn.close()

    capture_cohort()


gridskew_elexon_b1610_cohort()
