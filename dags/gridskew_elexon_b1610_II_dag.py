"""Load the B1610 Interim Information settlement position each day.

II is published after five working days. The 14-day offset also covers weekends
and holiday periods while the source position is still available.

Each run captures one British settlement date and asks for II by name. The
task fails when the stored date lacks a settlement period or the source
returns another run. Clear the failed task instance to repeat the capture
while II is still in force; do not trigger a manual run for an old date.
"""

import sys
from datetime import datetime, timedelta, timezone

from airflow.decorators import dag, task

# Namespaced per project, matching the bind-mount declared in the Airflow
# compose file. Deliberately not a global PYTHONPATH:
PROJECT_PATH = "/opt/airflow/project/gridskew"

RUN_TYPE = "II"
LAG = timedelta(days=14)


def settlement_date_to_capture(data_interval_start):
    """Return the British settlement date that one daily run captures."""

    return (data_interval_start - LAG).date()


@dag(
    dag_id="gridskew_elexon_b1610_II",
    schedule="@daily",
    start_date=datetime(2025, 9, 5, tzinfo=timezone.utc),
    catchup=True,
    max_active_runs=1,
    default_args={
        "retries": 3,
        "retry_delay": timedelta(minutes=5),
        "execution_timeout": timedelta(minutes=45),
    },
    tags=["gridskew", "elexon"],
)
def gridskew_elexon_b1610_II():

    @task(pool="elexon")
    def poll_b1610_II(data_interval_start=None):
        from airflow.providers.postgres.hooks.postgres import PostgresHook

        if data_interval_start is None:
            # Airflow 3 manual runs have no data interval (HL-10 §2).
            raise RuntimeError(
                "No data interval: clear a scheduled task instance to repeat a capture"
            )
        settlement_date = settlement_date_to_capture(data_interval_start)

        sys.path.insert(0, PROJECT_PATH)
        from ingestion.elexon.b1610_poller import capture_settlement_date

        # Credentials come from the Airflow Connection.
        conn = PostgresHook(postgres_conn_id="gridskew_prod").get_conn()
        try:
            capture_settlement_date(conn, settlement_date, RUN_TYPE)
        finally:
            conn.close()

    poll_b1610_II()


gridskew_elexon_b1610_II()
