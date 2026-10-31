"""Capture Elexon's national wind forecast (WINDFOR) and outturn (FUELHH WIND).

The run at 06:00 UTC targets the previous UTC day: its WINDFOR publish day
and the settlement date of the same name. poll_windfor and poll_fuelhh ask
for the target unless it is complete or held in quarantine, one request a
try, retried for up to ten hours. poll_windfor_look_back and
poll_fuelhh_look_back ask once for each of the seven days before the target
that is neither, newest first, with no retry: the next morning's run asks
again. Days are held from 1 November 2026. A held day fails its task each
morning, with no request, until its quarantine rows are dealt with.

A manual trigger has no data interval and is refused. Days older than the
look-back need `airflow backfill create`.
"""

import sys
from datetime import datetime, timedelta, timezone

from airflow.sdk import dag, task
from airflow.timetables.interval import CronDataIntervalTimetable

# The platform mounts the GridSkew checkout here.
PROJECT_PATH = "/opt/airflow/project/gridskew"


def target_day(data_interval_start):
    """The UTC day before the run: its interval's first day.

    At 06:00 UTC the London date equals the UTC date in GMT and in BST, so
    the previous settlement date is this day too.
    """

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
    dag_id="gridskew_elexon_wind",
    # Not "@daily": this timetable keeps a full-day data interval on Airflow 3.
    schedule=CronDataIntervalTimetable("0 6 * * *", timezone="UTC"),
    # The first interval starts on the first capture day, which is its target.
    start_date=datetime(2026, 11, 1, 6, 0, tzinfo=timezone.utc),
    # Each run's look-back asks for the days an outage missed.
    catchup=False,
    is_paused_upon_creation=True,
    max_active_runs=1,
    # The target tasks: one request a try, the first retry 30 minutes later,
    # doubling to 2 hours; the last try ends within 10 hours.
    default_args={
        "retries": 5,
        "retry_delay": timedelta(minutes=30),
        "retry_exponential_backoff": True,
        "max_retry_delay": timedelta(hours=2),
        "execution_timeout": timedelta(minutes=10),
    },
    tags=["gridskew", "elexon"],
)
def gridskew_elexon_wind():

    @task(pool="elexon")
    def poll_windfor(data_interval_start=None):
        refuse_a_run_without_a_data_interval(data_interval_start)

        sys.path.insert(0, PROJECT_PATH)

        from ingestion.dag_tasks import poll_into_gridskew_prod
        from ingestion.elexon.wind_poller import capture_windfor_target

        poll_into_gridskew_prod(capture_windfor_target, target_day(data_interval_start))

    @task(pool="elexon", retries=0, execution_timeout=timedelta(minutes=20))
    def poll_windfor_look_back(data_interval_start=None):
        refuse_a_run_without_a_data_interval(data_interval_start)

        sys.path.insert(0, PROJECT_PATH)

        from ingestion.dag_tasks import poll_into_gridskew_prod
        from ingestion.elexon.wind_poller import capture_windfor_look_back

        poll_into_gridskew_prod(
            capture_windfor_look_back, target_day(data_interval_start)
        )

    @task(pool="elexon")
    def poll_fuelhh(data_interval_start=None):
        refuse_a_run_without_a_data_interval(data_interval_start)

        sys.path.insert(0, PROJECT_PATH)

        from ingestion.dag_tasks import poll_into_gridskew_prod
        from ingestion.elexon.wind_poller import capture_fuelhh_target

        poll_into_gridskew_prod(capture_fuelhh_target, target_day(data_interval_start))

    @task(pool="elexon", retries=0, execution_timeout=timedelta(minutes=20))
    def poll_fuelhh_look_back(data_interval_start=None):
        refuse_a_run_without_a_data_interval(data_interval_start)

        sys.path.insert(0, PROJECT_PATH)

        from ingestion.dag_tasks import poll_into_gridskew_prod
        from ingestion.elexon.wind_poller import capture_fuelhh_look_back

        poll_into_gridskew_prod(
            capture_fuelhh_look_back, target_day(data_interval_start)
        )

    poll_windfor()
    poll_windfor_look_back()
    poll_fuelhh()
    poll_fuelhh_look_back()


gridskew_elexon_wind()
