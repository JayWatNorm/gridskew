"""Load Elexon REMIT messages each hour.

The poller takes its window from the table: each run loads everything
published since the newest stored message. The first run on an empty table
loads the whole history, a day at a time.
"""

import sys
from datetime import datetime, timedelta, timezone

from airflow.sdk import dag, task

# The platform mounts the GridSkew checkout here.
PROJECT_PATH = "/opt/airflow/project/gridskew"


@dag(
    dag_id="gridskew_elexon_remit",
    schedule="15 * * * *",
    start_date=datetime(2026, 10, 8, tzinfo=timezone.utc),
    catchup=False,
    max_active_runs=1,
    is_paused_upon_creation=True,
    default_args={
        "retries": 2,
        "retry_delay": timedelta(minutes=10),
        "execution_timeout": timedelta(minutes=10),
    },
    tags=["gridskew", "elexon"],
)
def gridskew_elexon_remit():

    @task(pool="elexon")
    def poll_remit():
        sys.path.insert(0, PROJECT_PATH)
        from ingestion.dag_tasks import poll_into_gridskew_prod
        from ingestion.elexon.remit_poller import run

        poll_into_gridskew_prod(run)

    poll_remit()


gridskew_elexon_remit()
