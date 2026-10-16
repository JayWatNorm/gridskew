"""Update the incremental period tables each night and test the project.

Scheduled runs only write rows: they run the incremental models, then every
test except those that re-read all history, and never recreate a view. Trigger
with {"full_refresh": true} after a model logic change or a settlement-run
seed change: that run rebuilds the tables and views that changed since the
last published run, with everything below them, and runs their tests. When
nothing changed, or a period table is missing, it rebuilds them all. After a
load outside Airflow or a raw edit, add "rebuild_all": true. A scheduled run
refuses to start while a period table or a view below one has changed since
the last published run, so a model change cannot reach the tables without
that full refresh. The DAG is created paused; enable it after an observed
full refresh.

Each dbt command records its node results in dbt_dev.dbt_node_results. A run
that succeeds publishes dbt's manifest and its last run results.
"""

import os
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from airflow.decorators import dag, task

PROJECT_PATH = "/opt/airflow/project/gridskew"
DBT_PROJECT = f"{PROJECT_PATH}/dbt"
DBT_PROFILES = "/opt/airflow/dbt_profiles"
DBT_LOGS = "/opt/airflow/data/gridskew/dbt_logs"
PERIOD_TABLES = (
    "int_elexon__b1610_period",
    "int_elexon__pn_period_mwh",
    "int_elexon__instruction_intervals",
    "int_shortfall_by_unit_period",
)
# Views the tables read that no scheduled command builds; a full refresh
# creates them first, so a new view reaches production with its table.
REFRESH_VIEWS = ("int_elexon__bm_unit_cohort", "stg_elexon__qas")
RECORD_NODE_RESULTS = ("--vars", "{audit: true}")
ARTIFACTS = "/opt/airflow/data/gridskew/artifacts"
ARTIFACT_FILES = ("manifest.json", "run_results.json")
ARTIFACT_DAYS_KEPT = 30
LOG_DAYS_KEPT = 30
# One budget for the whole task: a full refresh, about an hour today, grows
# with the B1610 table. A scheduled command stops sooner (run_dbt below).
TASK_TIME_LIMIT = timedelta(hours=4)
SCHEDULED_COMMAND_MINUTES = 45


def dbt_environment(work_dir, airflow_run_id):
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
    task_env["DBT_LOG_PATH"] = log_folder(airflow_run_id)
    task_env["GRIDSKEW_DBT_HOST"] = conn.host
    task_env["GRIDSKEW_DBT_PORT"] = str(conn.port or 5432)
    task_env["GRIDSKEW_DBT_USER"] = conn.login
    task_env["GRIDSKEW_DBT_PASSWORD"] = conn.password
    task_env["GRIDSKEW_DBT_NAME"] = conn.schema
    task_env["GRIDSKEW_AIRFLOW_RUN_ID"] = airflow_run_id
    return task_env


def log_folder(airflow_run_id, today=None):
    """A folder per run inside a folder per UTC day, so old days can be pruned."""

    today = today or datetime.now(timezone.utc).date()
    return f"{DBT_LOGS}/nightly/{today.isoformat()}/{airflow_run_id}"


def run_dbt(env, args, timeout_minutes):
    command = [
        "dbt",
        *args,
        *RECORD_NODE_RESULTS,
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
        timeout=None if timeout_minutes is None else timeout_minutes * 60,
    )
    print(result.stdout)
    print(result.stderr)
    result.check_returncode()


def dbt_commands(full_refresh, rebuild=(*REFRESH_VIEWS, *PERIOD_TABLES)):
    """The run-code test blocks before any period table is written. A full
    refresh rebuilds `rebuild` and everything below it.

    The selectors are in dbt/selectors.yml.
    """

    period_tables_and_descendants = []
    for relation in rebuild:
        period_tables_and_descendants.append(f"{relation}+")

    test_run_codes = ["test", "--selector", "nightly_run_code_check"]
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
    update_incremental_models = ["run", "--selector", "nightly_models"]
    test_project = ["test", "--selector", "nightly_tests"]

    if full_refresh:
        return [test_run_codes, test_sources, rebuild_period_tables_and_descendants]
    return [test_run_codes, update_incremental_models, test_project]


def models_needing_full_refresh(env, state_dir):
    """Period tables, and the views below them, whose SQL, configuration or
    upstream (a seed, a macro) changed since the published manifest. A
    scheduled run would leave their stored rows on the old logic. Nothing is
    reported before the first published manifest."""

    if not (Path(state_dir) / "manifest.json").exists():
        return []
    changed_below_a_period_table = []
    for relation in (*REFRESH_VIEWS, *PERIOD_TABLES):
        changed_below_a_period_table.append(f"state:modified+,{relation}+")
    command = [
        "dbt",
        "--quiet",
        "ls",
        "--resource-type",
        "model",
        "--output",
        "name",
        "--select",
        *changed_below_a_period_table,
        "--state",
        str(state_dir),
        *RECORD_NODE_RESULTS,
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
        timeout=SCHEDULED_COMMAND_MINUTES * 60,
    )
    print(result.stderr)
    result.check_returncode()
    return sorted(set(result.stdout.split()))


def relations_to_rebuild(changed, missing, rebuild_all):
    """What a full refresh rebuilds, with everything below: the models that
    changed. Every period table and view when nothing changed (a raw edit or
    a load outside Airflow cannot be seen), a table is missing, or rebuild_all."""

    if changed and not missing and not rebuild_all:
        return tuple(changed)
    return (*REFRESH_VIEWS, *PERIOD_TABLES)


def missing_period_tables(conn, tables=PERIOD_TABLES):
    qualified_names = []
    relation_per_table = []
    for table in tables:
        qualified_names.append(f"dbt_dev.{table}")
        relation_per_table.append("to_regclass(%s)")

    with conn.cursor() as cursor:
        cursor.execute(f"SELECT {', '.join(relation_per_table)}", qualified_names)
        relation_or_null_per_table = cursor.fetchone()

    missing = []
    for table, relation in zip(tables, relation_or_null_per_table, strict=True):
        if relation is None:
            missing.append(table)
    return missing


def publish_artifacts(target_dir, artifacts_dir, finished_at):
    """Copy dbt's artifacts to a folder for the UTC day and to latest/."""

    day_dir = Path(artifacts_dir) / finished_at.strftime("%Y-%m-%d")
    latest_dir = Path(artifacts_dir) / "latest"
    for publish_dir in (day_dir, latest_dir):
        publish_dir.mkdir(parents=True, exist_ok=True)
        for file_name in ARTIFACT_FILES:
            replace_file(Path(target_dir) / file_name, publish_dir / file_name)
    remove_day_folders_before(
        Path(artifacts_dir), finished_at.date() - timedelta(days=ARTIFACT_DAYS_KEPT)
    )


def replace_file(source, destination):
    """A reader sees the old file or the new one, never a part-written file."""

    part_written = destination.with_name(destination.name + ".part")
    shutil.copyfile(source, part_written)
    os.replace(part_written, destination)


def remove_day_folders_before(artifacts_dir, first_day_kept):
    for folder in artifacts_dir.iterdir():
        day = day_named_by(folder)
        if day is not None and day < first_day_kept:
            shutil.rmtree(folder)


def day_named_by(folder):
    """The date a day folder is named for; None for latest/ and anything else."""

    if not folder.is_dir():
        return None
    try:
        return datetime.strptime(folder.name, "%Y-%m-%d").date()
    except ValueError:
        return None


@dag(
    dag_id="gridskew__dbt_nightly",
    schedule="30 2 * * *",
    start_date=datetime(2026, 9, 27, tzinfo=timezone.utc),
    catchup=False,
    max_active_runs=1,
    is_paused_upon_creation=True,
    dagrun_timeout=TASK_TIME_LIMIT + timedelta(minutes=30),
    params={"full_refresh": False, "rebuild_all": False},
    default_args={"retries": 0},
    tags=["gridskew", "dbt"],
)
def gridskew__dbt_nightly():
    # One task holds the one-slot pool for the whole run, so another GridSkew
    # dbt job cannot start between its commands.
    @task(pool="gridskew_dbt", execution_timeout=TASK_TIME_LIMIT)
    def update_period_tables(params=None, run_id=None):
        from airflow.providers.postgres.hooks.postgres import PostgresHook

        full_refresh = bool((params or {}).get("full_refresh"))
        rebuild_all = bool((params or {}).get("rebuild_all"))
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
        today = datetime.now(timezone.utc).date()
        Path(log_folder(run_id, today)).mkdir(parents=True, exist_ok=True)
        # The BM-unit DAG writes its dbt logs the same way.
        for job in ("nightly", "bmunits"):
            job_logs = Path(DBT_LOGS) / job
            job_logs.mkdir(parents=True, exist_ok=True)
            remove_day_folders_before(job_logs, today - timedelta(days=LOG_DAYS_KEPT))
        with TemporaryDirectory(
            prefix="nightly-", dir="/opt/airflow/data/gridskew"
        ) as work_dir:
            env = dbt_environment(work_dir, run_id)
            changed = models_needing_full_refresh(env, Path(ARTIFACTS) / "latest")
            if changed and not full_refresh:
                raise RuntimeError(
                    f"Models changed since the last published run: {changed}. "
                    'Trigger once with {"full_refresh": true}.'
                )
            rebuild = relations_to_rebuild(changed, missing, rebuild_all)
            if full_refresh:
                print(
                    f"Full refresh rebuilds {sorted(rebuild)} and everything below them"
                )
            for args in dbt_commands(full_refresh, rebuild):
                # 45 minutes: the first night after a catch-up capture can
                # recompute every half-hour with an acceptance (about 25 min).
                # A full refresh is bounded by the task's time limit only.
                run_dbt(
                    env,
                    args,
                    timeout_minutes=None if full_refresh else SCHEDULED_COMMAND_MINUTES,
                )
            publish_artifacts(
                env["DBT_TARGET_PATH"], ARTIFACTS, datetime.now(timezone.utc)
            )

    update_period_tables()


gridskew__dbt_nightly()
