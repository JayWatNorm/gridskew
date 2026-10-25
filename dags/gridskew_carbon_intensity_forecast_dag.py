"""Archive each half-hourly NESO Carbon Intensity forecast revision."""

import sys
from datetime import datetime, timedelta, timezone

from airflow.sdk import dag, task

# The platform mounts the GridSkew checkout here.
PROJECT_PATH = "/opt/airflow/project/gridskew"

# int_carbon_forecast__revisions counts the first poll of each UTC half-hour.
POLL_SLOT_MINUTES = 30


def slot_start(moment):
    return moment.replace(
        minute=moment.minute - moment.minute % POLL_SLOT_MINUTES,
        second=0,
        microsecond=0,
    )


def is_past_its_slot(scheduled_at, now):
    """A late or cleared run would store today's forecast in a later slot."""

    return slot_start(now.astimezone(timezone.utc)) > slot_start(
        scheduled_at.astimezone(timezone.utc)
    )


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
def gridskew_carbon_intensity_forecast():
    """The dag_id predates the forecast name and is kept for its run history."""

    # The five-minute schedule offset allows NESO's boundary update to finish.
    @task()
    def poll_forecast(dag_run=None, data_interval_end=None):
        from airflow.sdk import BaseHook
        from airflow.sdk.exceptions import AirflowFailException, AirflowSkipException

        # The archive counts the first poll of each half-hour, so only a
        # scheduled run in its own slot may poll: a manual poll could take
        # the slot's place, and a re-run cannot fetch the forecast it missed.
        if dag_run is None or dag_run.run_type != "scheduled":
            raise AirflowSkipException(
                "Only a scheduled forecast run may poll: a manual run would "
                "displace the slot's scheduled poll"
            )
        if is_past_its_slot(data_interval_end, datetime.now(timezone.utc)):
            raise AirflowSkipException(
                "Past its half-hour slot: a late or cleared forecast run "
                "cannot recover the poll it missed"
            )

        sys.path.insert(0, PROJECT_PATH)
        from ingestion.carbon_intensity.forecast_poller import run
        from ingestion.database import connect
        from ingestion.routing import ContractRejected

        conn = connect(BaseHook.get_connection("gridskew_prod"))
        try:
            run(conn)
        except ContractRejected as rejected:
            raise AirflowFailException(str(rejected)) from rejected
        finally:
            conn.close()

    poll_forecast()


gridskew_carbon_intensity_forecast()
