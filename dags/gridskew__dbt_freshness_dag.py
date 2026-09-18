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
        import os

        project_dir = "/opt/airflow/project/gridskew/dbt"
        profiles_dir = "/opt/airflow/dbt_profiles"
        log_path = "/opt/airflow/data/gridskew/dbt_logs"

        task_env = os.environ.copy()
        task_env["DBT_TARGET_PATH"] = "/opt/airflow/data/gridskew/dbt_target"
        task_env["DBT_PACKAGES_INSTALL_PATH"] = (
            "/opt/airflow/data/gridskew/dbt_packages"
        )

        # Parse the Airflow URI into pieces for dbt's profile
        from urllib.parse import urlparse

        uri = urlparse(os.environ["AIRFLOW_CONN_GRIDSKEW_PROD"])
        task_env["GRIDSKEW_DB_HOST"] = uri.hostname
        task_env["GRIDSKEW_DB_PORT"] = str(uri.port)
        task_env["GRIDSKEW_DB_USER"] = uri.username
        task_env["GRIDSKEW_DB_PASSWORD"] = uri.password
        task_env["GRIDSKEW_DB_NAME"] = uri.path.lstrip("/")

        for cmd in (["dbt", "source", "freshness"],):
            result = subprocess.run(
                cmd
                + [
                    "--project-dir",
                    project_dir,
                    "--profiles-dir",
                    profiles_dir,
                    "--log-path",
                    log_path,
                ],
                capture_output=True,
                text=True,
                check=False,
                env=task_env,
            )
            print(result.stdout)
            print(result.stderr)
            result.check_returncode()

    freshness_check()


gridskew__dbt_freshness_dag()
