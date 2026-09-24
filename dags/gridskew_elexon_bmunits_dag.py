"""Capture a complete BM-unit registry and update observed dbt history."""

import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from tempfile import TemporaryDirectory
from uuid import uuid4

from airflow.decorators import dag, task

PROJECT_PATH = "/opt/airflow/project/gridskew"
DBT_PROJECT = f"{PROJECT_PATH}/dbt"
DBT_PROFILES = "/opt/airflow/dbt_profiles"
DBT_LOGS = "/opt/airflow/data/gridskew/dbt_logs"


def dbt_environment(work_dir):
    """Use the dbt role in the production database's development schemas."""

    from airflow.hooks.base import BaseHook

    raw_conn = BaseHook.get_connection("gridskew_prod")
    conn = BaseHook.get_connection("gridskew_dbt")
    if (
        not conn.schema
        or conn.schema != raw_conn.schema
        or conn.host != raw_conn.host
        or (conn.port or 5432) != (raw_conn.port or 5432)
    ):
        raise RuntimeError(
            "gridskew_dbt must point to the GridSkew production database"
        )
    if not conn.login or not conn.password:
        raise RuntimeError("gridskew_dbt needs a database user and password")
    task_env = os.environ.copy()
    task_env["DBT_TARGET_PATH"] = f"{work_dir}/target"
    task_env["DBT_PACKAGES_INSTALL_PATH"] = f"{work_dir}/packages"
    task_env["DBT_LOG_PATH"] = f"{DBT_LOGS}/bmunits/{uuid4()}"
    task_env["GRIDSKEW_DBT_HOST"] = conn.host
    task_env["GRIDSKEW_DBT_PORT"] = str(conn.port or 5432)
    task_env["GRIDSKEW_DBT_USER"] = conn.login
    task_env["GRIDSKEW_DBT_PASSWORD"] = conn.password
    task_env["GRIDSKEW_DBT_NAME"] = conn.schema
    task_env["GRIDSKEW_DB_HOST"] = raw_conn.host
    task_env["GRIDSKEW_DB_PORT"] = str(raw_conn.port or 5432)
    task_env["GRIDSKEW_DB_USER"] = raw_conn.login
    task_env["GRIDSKEW_DB_PASSWORD"] = raw_conn.password
    task_env["GRIDSKEW_DB_NAME"] = raw_conn.schema
    return task_env


def run_dbt(env, *args):
    command = [
        "dbt",
        *args,
        "--project-dir",
        DBT_PROJECT,
        "--profiles-dir",
        DBT_PROFILES,
        "--target",
        "dev",
        "--log-path",
        env["DBT_LOG_PATH"],
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        env=env,
        timeout=14 * 60,
    )
    print(result.stdout)
    print(result.stderr)
    result.check_returncode()


@dag(
    dag_id="gridskew_elexon_bmunits",
    schedule="@daily",
    start_date=datetime(2026, 9, 23, tzinfo=timezone.utc),
    catchup=False,
    max_active_runs=1,
    is_paused_upon_creation=True,
    default_args={"retries": 2, "retry_delay": timedelta(minutes=5)},
    tags=["gridskew", "elexon", "dbt"],
)
def gridskew_elexon_bmunits():
    @task(pool="elexon", execution_timeout=timedelta(minutes=15))
    def capture_registry():
        from airflow.providers.postgres.hooks.postgres import PostgresHook

        sys.path.insert(0, PROJECT_PATH)
        from ingestion.elexon.bmunits_poller import run

        conn = PostgresHook(postgres_conn_id="gridskew_prod").get_conn()
        try:
            return str(run(conn))
        finally:
            conn.close()

    @task(execution_timeout=timedelta(minutes=60))
    def build_current_and_snapshot(extract_id):
        from airflow.providers.postgres.hooks.postgres import PostgresHook

        sys.path.insert(0, PROJECT_PATH)
        from ingestion.elexon.bmunits_poller import locked_extract

        conn = PostgresHook(postgres_conn_id="gridskew_prod").get_conn()
        try:
            with (
                TemporaryDirectory(
                    prefix="bmunits-", dir="/opt/airflow/data/gridskew"
                ) as work_dir,
                locked_extract(conn, extract_id),
            ):
                env = dbt_environment(work_dir)
                print(f"BM-unit dbt logs: {env['DBT_LOG_PATH']}")
                run_dbt(env, "seed", "--select", "elexon_fuel_codes")
                run_dbt(env, "build", "--select", "+dim_bm_unit")
                run_dbt(
                    env,
                    "test",
                    "--select",
                    "assert_bm_units_staging_grain",
                    "assert_bm_units_current_extract_count",
                    "assert_bm_units_duplicate_attributes_agree",
                )
                run_dbt(env, "snapshot", "--select", "snap_elexon__bm_units")
        finally:
            conn.close()

    build_current_and_snapshot(capture_registry())


gridskew_elexon_bmunits()
