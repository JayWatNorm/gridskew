"""Guard the deployed BM-unit dbt target without requiring Airflow to run."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fakes import load_dag_file


@pytest.fixture
def connections():
    return {
        "gridskew_prod": SimpleNamespace(
            host="postgres",
            port=5432,
            schema="gridskew_prod",
            login="raw_writer",
            password="test-only-raw",
        ),
        "gridskew_dbt": SimpleNamespace(
            host="postgres",
            port=5432,
            schema="gridskew_prod",
            login="gridskew_dbt",
            password="test-only",
        ),
    }


@pytest.fixture
def dag_helpers(monkeypatch, connections):
    return load_dag_file(monkeypatch, "gridskew_elexon_bmunits_dag.py", connections)


def test_dbt_runs_as_the_dbt_role_in_the_dev_target(dag_helpers, monkeypatch):
    env = dag_helpers["dbt_environment"]("/tmp/bmunits", "scheduled__2026-10-12")
    assert env["GRIDSKEW_DBT_NAME"] == "gridskew_prod"
    assert env["GRIDSKEW_DBT_USER"] == "gridskew_dbt"
    assert env["GRIDSKEW_DB_USER"] == "raw_writer"

    finished_process = SimpleNamespace(stdout="", stderr="", check_returncode=Mock())
    run = Mock(return_value=finished_process)
    monkeypatch.setattr(dag_helpers["subprocess"], "run", run)
    dag_helpers["run_dbt"](env, "seed", "--select", "elexon_fuel_codes")
    command = run.call_args.args[0]
    assert command[command.index("--target") + 1] == "dev"


def test_two_runs_log_to_two_folders(dag_helpers):
    scheduled = dag_helpers["dbt_environment"]("/tmp/bmunits", "scheduled__2026-10-12")
    manual = dag_helpers["dbt_environment"]("/tmp/bmunits", "manual__2026-10-12")

    assert scheduled["DBT_LOG_PATH"] != manual["DBT_LOG_PATH"]


@pytest.mark.parametrize(
    "field,value,refusal",
    [
        ("schema", "gridskew_dev", "production database"),
        ("host", "other", "production database"),
        ("port", 5433, "production database"),
        ("login", None, "user and password"),
        ("password", None, "user and password"),
    ],
)
def test_rejects_a_dbt_connection_it_cannot_trust(
    dag_helpers, connections, field, value, refusal
):
    setattr(connections["gridskew_dbt"], field, value)
    with pytest.raises(RuntimeError, match=refusal):
        dag_helpers["dbt_environment"]("/tmp/bmunits", "manual__1")


def test_rejects_connections_that_name_no_database(dag_helpers, connections):
    connections["gridskew_prod"].schema = None
    connections["gridskew_dbt"].schema = None
    with pytest.raises(RuntimeError, match="production database"):
        dag_helpers["dbt_environment"]("/tmp/bmunits", "manual__1")
