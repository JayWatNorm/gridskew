"""Update the S6 period tables each night and test them.

Scheduled runs only write data: they run the two incremental period tables and
their tests, and never recreate views. Trigger with {"full_refresh": true} after
a load outside Airflow, a raw edit, a settlement-run seed change, a model logic
change, and monthly; that run rebuilds the tables and fact views and runs every
test. The DAG is created paused; enable it after an observed full refresh.
"""

import os
import subprocess
from datetime import datetime, timedelta, timezone
from tempfile import TemporaryDirectory
from uuid import uuid4

from airflow.decorators import dag, task

PROJECT_PATH = "/opt/airflow/project/gridskew"
DBT_PROJECT = f"{PROJECT_PATH}/dbt"
DBT_PROFILES = "/opt/airflow/dbt_profiles"
DBT_LOGS = "/opt/airflow/data/gridskew/dbt_logs"
PERIOD_TABLES = ("int_elexon__b1610_period", "int_elexon__pn_period_mwh")


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
    task_env["DBT_LOG_PATH"] = f"{DBT_LOGS}/nightly/{uuid4()}"
    task_env["GRIDSKEW_DBT_HOST"] = conn.host
    task_env["GRIDSKEW_DBT_PORT"] = str(conn.port or 5432)
    task_env["GRIDSKEW_DBT_USER"] = conn.login
    task_env["GRIDSKEW_DBT_PASSWORD"] = conn.password
    task_env["GRIDSKEW_DBT_NAME"] = conn.schema
    return task_env


def run_dbt(env, args, timeout_minutes):
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
        timeout=timeout_minutes * 60,
    )
    print(result.stdout)
    print(result.stderr)
    result.check_returncode()


def dbt_commands(full_refresh):
    """The run-code test blocks before any period table is written."""

    period_tables_and_descendants = []
    for table in PERIOD_TABLES:
        period_tables_and_descendants.append(f"{table}+")

    test_run_codes = ["test", "--select", "stg_elexon__b1610,test_type:generic"]
    test_sources = [
        "test",
        "--select",
        "source:elexon.elexon_b1610",
        "source:elexon.elexon_pn",
    ]
    rebuild_period_tables_and_descendants = [
        "build",
        "--select",
        *period_tables_and_descendants,
        "--full-refresh",
    ]
    update_period_tables = ["run", "--select", *PERIOD_TABLES]
    test_period_tables = [
        "test",
        "--select",
        *period_tables_and_descendants,
        "--exclude",
        "tag:full_population",
    ]

    if full_refresh:
        return [test_run_codes, test_sources, rebuild_period_tables_and_descendants]
    return [test_run_codes, update_period_tables, test_period_tables]


def missing_period_tables(conn):
    qualified_names = []
    for table in PERIOD_TABLES:
        qualified_names.append(f"dbt_dev.{table}")

    with conn.cursor() as cursor:
        cursor.execute("SELECT to_regclass(%s), to_regclass(%s)", qualified_names)
        relation_or_null_per_table = cursor.fetchone()

    missing = []
    for table, relation in zip(PERIOD_TABLES, relation_or_null_per_table, strict=True):
        if relation is None:
            missing.append(table)
    return missing


@dag(
    dag_id="gridskew__dbt_nightly",
    schedule="30 2 * * *",
    start_date=datetime(2026, 9, 27, tzinfo=timezone.utc),
    catchup=False,
    max_active_runs=1,
    is_paused_upon_creation=True,
    dagrun_timeout=timedelta(hours=2, minutes=30),
    params={"full_refresh": False},
    default_args={"retries": 0},
    tags=["gridskew", "dbt", "s6"],
)
def gridskew__dbt_nightly():
    # One task holds the one-slot pool for the whole run, so another GridSkew
    # dbt job cannot start between its commands.
    @task(pool="gridskew_dbt", execution_timeout=timedelta(hours=2))
    def update_period_tables(params=None):
        from airflow.providers.postgres.hooks.postgres import PostgresHook

        full_refresh = bool((params or {}).get("full_refresh"))
        conn = PostgresHook(postgres_conn_id="gridskew_dbt").get_conn()
        try:
            missing = missing_period_tables(conn)
        finally:
            conn.close()
        if missing and not full_refresh:
            raise RuntimeError(
                f"Period tables missing: {missing}. Trigger once with "
                '{"full_refresh": true} and observe it before scheduled runs.'
            )
        with TemporaryDirectory(
            prefix="nightly-", dir="/opt/airflow/data/gridskew"
        ) as work_dir:
            env = dbt_environment(work_dir)
            for args in dbt_commands(full_refresh):
                run_dbt(env, args, timeout_minutes=110 if full_refresh else 30)

    update_period_tables()


gridskew__dbt_nightly()
