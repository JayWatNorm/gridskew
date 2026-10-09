"""Run the nightly DAG's default dbt commands for real in CI's PostgreSQL.

Run after the full `dbt build`, from the repository root, with PYTHONPATH set
to it. It checks that:

- an invocation without `audit: true` leaves no audit table;
- the three nightly commands succeed, and each node of each command gets one
  row in dbt_dev.dbt_node_results under the Airflow run ID;
- no view is recreated by the nightly commands;
- a row older than the retention period is deleted, a younger one is kept,
  and a run outside Airflow stores a null run ID.
"""

import json
import os
import subprocess
from pathlib import Path

import psycopg2

from tests.adhoc.check_scheduled_set import nightly_default_commands

PROJECT_DIR = Path(__file__).resolve().parents[2] / "dbt"
AUDIT_TABLE = "dbt_dev.dbt_node_results"
RECORD_NODE_RESULTS = ["--vars", "{audit: true}"]
AIRFLOW_RUN_ID = "ci__nightly_check"
VIEW_IDENTITIES = """
    SELECT c.relname, c.oid
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'dbt_dev' AND c.relkind = 'v'
    ORDER BY c.relname
"""


def disposable_connection():
    return psycopg2.connect(
        host=os.environ["DBT_HOST"],
        port=os.environ["DBT_PORT"],
        user=os.environ["DBT_USER"],
        password=os.environ["DBT_PASSWORD"],
        dbname=os.environ["DBT_DBNAME"],
    )


def run_dbt(args, airflow_run_id):
    """Run one dbt command and return the node IDs in its run results."""

    env = os.environ.copy()
    env.pop("GRIDSKEW_AIRFLOW_RUN_ID", None)
    if airflow_run_id is not None:
        env["GRIDSKEW_AIRFLOW_RUN_ID"] = airflow_run_id
    subprocess.run(
        ["dbt", *args, "--project-dir", str(PROJECT_DIR)], check=True, env=env
    )

    target = Path(os.getenv("DBT_TARGET_PATH", str(PROJECT_DIR / "target")))
    run_results = json.loads((target / "run_results.json").read_text(encoding="utf-8"))
    node_ids = []
    for result in run_results["results"]:
        # The hook that writes the audit rows is itself a result; it cannot
        # record its own outcome.
        if not result["unique_id"].startswith("operation."):
            node_ids.append(result["unique_id"])
    return run_results["metadata"]["invocation_id"], sorted(node_ids)


def fetch_all(conn, query, parameters=None):
    with conn.cursor() as cursor:
        cursor.execute(query, parameters)
        rows = cursor.fetchall()
    conn.rollback()
    return rows


def audit_table_exists(conn):
    ((relation,),) = fetch_all(conn, "SELECT to_regclass(%s)", (AUDIT_TABLE,))
    return relation is not None


def audited_node_ids(conn, invocation_id, airflow_run_id):
    rows = fetch_all(
        conn,
        f"SELECT node_id FROM {AUDIT_TABLE} "
        "WHERE invocation_id = %s AND airflow_run_id IS NOT DISTINCT FROM %s "
        "ORDER BY node_id",
        (invocation_id, airflow_run_id),
    )
    node_ids = []
    for (node_id,) in rows:
        node_ids.append(node_id)
    return node_ids


def check_no_audit_without_the_variable(conn):
    run_dbt(["test", "--selector", "nightly_run_code_check"], AIRFLOW_RUN_ID)
    if audit_table_exists(conn):
        raise AssertionError("An invocation without audit: true wrote the audit table")


def check_nightly_commands_are_audited_and_keep_views(conn):
    views_before = fetch_all(conn, VIEW_IDENTITIES)
    if not views_before:
        raise AssertionError("No view in dbt_dev; run the full dbt build first")

    for command in nightly_default_commands():
        invocation_id, node_ids = run_dbt(
            [*command, *RECORD_NODE_RESULTS], AIRFLOW_RUN_ID
        )
        if not node_ids:
            raise AssertionError(f"dbt {' '.join(command)} selected nothing")
        audited = audited_node_ids(conn, invocation_id, AIRFLOW_RUN_ID)
        if audited != node_ids:
            raise AssertionError(
                f"dbt {' '.join(command)}: audit rows {audited}; expected {node_ids}"
            )

    if fetch_all(conn, VIEW_IDENTITIES) != views_before:
        raise AssertionError("A nightly command recreated a view")


def insert_audit_row_aged(conn, node_id, days_old):
    with conn.cursor() as cursor:
        cursor.execute(
            f"INSERT INTO {AUDIT_TABLE} "
            "(invocation_id, dbt_command, node_id, resource_type, status, "
            "recorded_at) "
            "VALUES ('ci-aged', 'test', %s, 'test', 'pass', "
            "now() - make_interval(days => %s))",
            (node_id, days_old),
        )
    conn.commit()


def check_retention_and_a_run_outside_airflow(conn):
    insert_audit_row_aged(conn, "ci.older_than_retention", 181)
    insert_audit_row_aged(conn, "ci.inside_retention", 179)

    invocation_id, node_ids = run_dbt(
        ["test", "--selector", "nightly_run_code_check", *RECORD_NODE_RESULTS],
        airflow_run_id=None,
    )

    if audited_node_ids(conn, invocation_id, None) != node_ids:
        raise AssertionError("A run outside Airflow must store a null run ID")
    if audited_node_ids(conn, "ci-aged", None) != ["ci.inside_retention"]:
        raise AssertionError("Retention must delete only rows older than 180 days")


def main():
    if (
        os.getenv("GRIDSKEW_DISPOSABLE_TEST") != "1"
        or os.getenv("DBT_HOST") not in {"localhost", "127.0.0.1"}
        or os.getenv("DBT_DBNAME") != "gridskew_dev"
    ):
        raise RuntimeError("Nightly checks require opt-in to local gridskew_dev")
    conn = disposable_connection()
    try:
        check_no_audit_without_the_variable(conn)
        check_nightly_commands_are_audited_and_keep_views(conn)
        check_retention_and_a_run_outside_airflow(conn)
        print("Nightly commands, node-result audit, retention and views passed")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
