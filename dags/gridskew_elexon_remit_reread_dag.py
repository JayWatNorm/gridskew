"""Re-read the previous seven days of Elexon REMIT messages each morning.

The run at 04:40 UTC asks again for each of the seven UTC publish days up to
the day before, oldest first, one request a day. Repeats store nothing; a
message the hourly run missed is stored with this run's time as its
retrieved_at. Nothing later than the hourly run's newest stored message is
asked for or stored, so the hourly window never moves. The task has no
retry: it stops at once on HTTP 429, and the next morning asks again. While
any REMIT row is in quarantine it makes no request and fails.

A manual trigger has no data interval and is refused. Days older than the
seven need `airflow backfill create`.
"""

import sys
from datetime import datetime, timedelta, timezone

from airflow.sdk import dag, task
from airflow.timetables.interval import CronDataIntervalTimetable

# The platform mounts the GridSkew checkout here.
PROJECT_PATH = "/opt/airflow/project/gridskew"


def last_reread_day(data_interval_start):
    """The UTC day before the run: its interval's first day."""

    return data_interval_start.astimezone(timezone.utc).date()


def refuse_a_run_without_a_data_interval(data_interval_start):
    from airflow.sdk.exceptions import AirflowFailException

    if data_interval_start is None:
        # Airflow 3 manual runs have no data interval.
        raise AirflowFailException(
            "No data interval: clear a scheduled task instance to repeat a capture, "
            "or use `airflow backfill create` for past days"
        )


@dag(
    dag_id="gridskew_elexon_remit_reread",
    # Not "@daily": this timetable keeps a full-day data interval on Airflow 3.
    schedule=CronDataIntervalTimetable("40 4 * * *", timezone="UTC"),
    start_date=datetime(2026, 11, 1, 4, 40, tzinfo=timezone.utc),
    # Each run covers seven days, so a missed morning needs no catch-up run.
    catchup=False,
    is_paused_upon_creation=True,
    max_active_runs=1,
    tags=["gridskew", "elexon"],
)
def gridskew_elexon_remit_reread():

    @task(pool="elexon", retries=0, execution_timeout=timedelta(minutes=15))
    def reread_remit(data_interval_start=None):
        refuse_a_run_without_a_data_interval(data_interval_start)

        sys.path.insert(0, PROJECT_PATH)

        from ingestion.dag_tasks import poll_into_gridskew_prod
        from ingestion.elexon.remit_poller import reread

        poll_into_gridskew_prod(reread, last_reread_day(data_interval_start))

    reread_remit()


gridskew_elexon_remit_reread()
