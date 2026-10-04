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


def test_s4_dbt_uses_dev_schema_target(dag_helpers, monkeypatch):
    env = dag_helpers["dbt_environment"]("/tmp/bmunits")
    assert env["GRIDSKEW_DBT_NAME"] == "gridskew_prod"
    assert env["GRIDSKEW_DBT_USER"] == "gridskew_dbt"
    assert env["GRIDSKEW_DB_USER"] == "raw_writer"

    finished_process = SimpleNamespace(stdout="", stderr="", check_returncode=Mock())
    run = Mock(return_value=finished_process)
    monkeypatch.setattr(dag_helpers["subprocess"], "run", run)
    dag_helpers["run_dbt"](env, "seed", "--select", "elexon_fuel_codes")
    command = run.call_args.args[0]
    assert command[command.index("--target") + 1] == "dev"
    assert command[:2] == ["dbt", "seed"]


@pytest.mark.parametrize("field,value", [("host", "other"), ("schema", "gridskew_dev")])
def test_s4_rejects_dbt_connection_outside_production(
    dag_helpers, connections, field, value
):
    setattr(connections["gridskew_dbt"], field, value)
    with pytest.raises(RuntimeError, match="production database"):
        dag_helpers["dbt_environment"]("/tmp/bmunits")
