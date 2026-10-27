"""The environment the DAGs run dbt in, as the GridSkew dbt role."""

import os

from ingestion.database import DEFAULT_PORT


def dbt_environment(work_dir, log_path):
    """Use the dbt role in the production database's development schemas.

    dbt keeps its compiled files and packages under `work_dir` and writes its
    logs to `log_path`.
    """

    from airflow.sdk import BaseHook

    raw_conn = BaseHook.get_connection("gridskew_prod")
    conn = BaseHook.get_connection("gridskew_dbt")
    if (
        not conn.schema
        or conn.schema != raw_conn.schema
        or conn.host != raw_conn.host
        or (conn.port or DEFAULT_PORT) != (raw_conn.port or DEFAULT_PORT)
    ):
        raise RuntimeError(
            "gridskew_dbt must point to the GridSkew production database"
        )
    if not conn.login or not conn.password:
        raise RuntimeError("gridskew_dbt needs a database user and password")
    task_env = os.environ.copy()
    task_env["DBT_TARGET_PATH"] = f"{work_dir}/target"
    task_env["DBT_PACKAGES_INSTALL_PATH"] = f"{work_dir}/packages"
    task_env["DBT_LOG_PATH"] = log_path
    task_env["GRIDSKEW_DBT_HOST"] = conn.host
    task_env["GRIDSKEW_DBT_PORT"] = str(conn.port or DEFAULT_PORT)
    task_env["GRIDSKEW_DBT_USER"] = conn.login
    task_env["GRIDSKEW_DBT_PASSWORD"] = conn.password
    task_env["GRIDSKEW_DBT_NAME"] = conn.schema
    return task_env
