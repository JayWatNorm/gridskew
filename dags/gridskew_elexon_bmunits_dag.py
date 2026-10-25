"""Capture a complete BM-unit registry and update observed dbt history."""

import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from tempfile import TemporaryDirectory

from airflow.sdk import dag, task

PROJECT_PATH = "/opt/airflow/project/gridskew"
DBT_PROJECT = f"{PROJECT_PATH}/dbt"
DBT_PROFILES = "/opt/airflow/dbt_profiles"
DBT_LOGS = "/opt/airflow/data/gridskew/dbt_logs"


def dbt_environment(work_dir, airflow_run_id):
    """Use the dbt role in the production database's development schemas."""

    from airflow.sdk import BaseHook

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
    # A folder per run inside a folder per UTC day; the nightly DAG removes
    # day folders older than 30 days.
    today = datetime.now(timezone.utc).date().isoformat()
    task_env["DBT_LOG_PATH"] = f"{DBT_LOGS}/bmunits/{today}/{airflow_run_id}"
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


def dbt_commands():
    """Seed, test and snapshot the registry chain; never build it.

    Test, not build: rebuilding these views would drop the views that depend
    on them (DROP ... CASCADE). Releases recreate views. Cautious selection:
    only tests whose parents are all in the registry chain or the seed, so no
    test that also reads B1610 runs here; the nightly job runs those.
    """

    load_fuel_codes = ["seed", "--select", "elexon_fuel_codes"]
    test_registry_chain = [
        "test",
        "--select",
        "+int_elexon__bm_units_current",
        "elexon_fuel_codes",
        "--indirect-selection",
        "cautious",
    ]
    snapshot_registry = ["snapshot", "--select", "snap_elexon__bm_units"]
    return [load_fuel_codes, test_registry_chain, snapshot_registry]


@dag(
    dag_id="gridskew_elexon_bmunits",
    schedule="@daily",
    start_date=datetime(2026, 9, 23, tzinfo=timezone.utc),
    catchup=False,
    max_active_runs=1,
    is_paused_upon_creation=True,
    # Set accept_shrink on one manual trigger only, after checking that Elexon
    # really removed more than five percent of the units.
    params={"accept_shrink": False},
    default_args={"retries": 2, "retry_delay": timedelta(minutes=5)},
    tags=["gridskew", "elexon", "dbt"],
)
def gridskew_elexon_bmunits():
    @task(pool="elexon", execution_timeout=timedelta(minutes=15))
    def capture_registry(params=None):
        from airflow.sdk import BaseHook

        sys.path.insert(0, PROJECT_PATH)
        from ingestion.database import connect
        from ingestion.elexon.bmunits_poller import run

        accept_shrink = bool((params or {}).get("accept_shrink"))
        conn = connect(BaseHook.get_connection("gridskew_prod"))
        try:
            return str(run(conn, accept_shrink=accept_shrink))
        finally:
            conn.close()

    @task(
        task_id="build_current_and_snapshot",
        pool="gridskew_dbt",
        execution_timeout=timedelta(minutes=60),
    )
    def seed_test_and_snapshot_registry(extract_id, run_id=None):
        """Seed, test and snapshot the registry, as dbt_commands lists.

        The task id predates this name and is kept for its run history.
        """

        from airflow.sdk import BaseHook

        sys.path.insert(0, PROJECT_PATH)
        from ingestion.database import connect
        from ingestion.elexon.bmunits_poller import locked_extract

        conn = connect(BaseHook.get_connection("gridskew_prod"))
        try:
            with (
                TemporaryDirectory(
                    prefix="bmunits-", dir="/opt/airflow/data/gridskew"
                ) as work_dir,
                locked_extract(conn, extract_id),
            ):
                env = dbt_environment(work_dir, run_id)
                print(f"BM-unit dbt logs: {env['DBT_LOG_PATH']}")
                for args in dbt_commands():
                    run_dbt(env, *args)
        finally:
            conn.close()

    seed_test_and_snapshot_registry(capture_registry())


gridskew_elexon_bmunits()
