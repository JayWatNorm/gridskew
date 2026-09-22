"""Source Freshness Check"""

import os
import subprocess
from datetime import datetime, timedelta, timezone

from airflow.decorators import dag, task


@dag(
    dag_id="gridskew__dbt_freshness_dag",
    schedule="20,50 * * * *",
    start_date=datetime(2026, 9, 18, tzinfo=timezone.utc),
    catchup=False,
    max_active_runs=1,
    default_args={
        "retries": 1,
        "retry_delay": timedelta(minutes=5),
        "execution_timeout": timedelta(minutes=15),
    },
    tags=["gridskew", "dbt", "freshness"],
)
def gridskew__dbt_freshness_dag():
    @task
    def freshness_check():
        from airflow.hooks.base import BaseHook

        project_dir = "/opt/airflow/project/gridskew/dbt"
        profiles_dir = "/opt/airflow/dbt_profiles"
        log_path = "/opt/airflow/data/gridskew/dbt_logs"

        task_env = os.environ.copy()
        task_env["DBT_TARGET_PATH"] = "/opt/airflow/data/gridskew/dbt_target"
        task_env["DBT_PACKAGES_INSTALL_PATH"] = (
            "/opt/airflow/data/gridskew/dbt_packages"
        )

        conn = BaseHook.get_connection("gridskew_prod")
        task_env["GRIDSKEW_DB_HOST"] = conn.host
        task_env["GRIDSKEW_DB_PORT"] = str(conn.port or 5432)
        task_env["GRIDSKEW_DB_USER"] = conn.login
        task_env["GRIDSKEW_DB_PASSWORD"] = conn.password
        task_env["GRIDSKEW_DB_NAME"] = conn.schema or "gridskew_prod"

        cmd = [
            "dbt",
            "source",
            "freshness",
            "--project-dir",
            project_dir,
            "--profiles-dir",
            profiles_dir,
            "--log-path",
            log_path,
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            env=task_env,
            timeout=14 * 60,
        )
        print(result.stdout)
        print(result.stderr)
        result.check_returncode()

    freshness_check()


gridskew__dbt_freshness_dag()
