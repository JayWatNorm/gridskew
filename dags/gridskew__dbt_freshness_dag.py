"""Source Freshness Check"""

import subprocess
from datetime import datetime, timedelta, timezone

from airflow.decorators import dag, task


@dag(
    dag_id="gridskew__dbt_freshness_dag",
    schedule="20,50 * * * *",
    start_date=datetime(2026, 9, 18, tzinfo=timezone.utc),
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 1, "retry_delay": timedelta(minutes=5)},
    tags=["gridskew", "dbt", "freshness"],
)
def gridskew__dbt_freshness_dag():

    @task
    def freshness_check():
        project_dir = "/opt/airflow/project/gridskew/dbt"
        profiles_dir = "/opt/airflow/dbt_profiles"
        log_path = "/opt/airflow/data/gridskew/dbt_logs"
        for cmd in (["dbt", "deps"], ["dbt", "source", "freshness"]):
            subprocess.run(
                cmd + ["--project-dir", project_dir, "--profiles-dir", profiles_dir, "--log-path", log_path],
                check=True
            )


    freshness_check()


gridskew__dbt_freshness_dag()
