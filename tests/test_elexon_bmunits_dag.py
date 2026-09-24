"""Guard the deployed BM-unit dbt target without requiring Airflow to run."""

import runpy
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.fixture
def dag_helpers(monkeypatch):
    airflow = ModuleType("airflow")
    airflow.__path__ = []
    decorators = ModuleType("airflow.decorators")
    decorators.dag = lambda **_kwargs: lambda _function: lambda: None
    decorators.task = Mock()
    hooks = ModuleType("airflow.hooks")
    hooks.__path__ = []
    base = ModuleType("airflow.hooks.base")
    connections = {
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
    base.BaseHook = SimpleNamespace(get_connection=connections.__getitem__)
    for name, module in {
        "airflow": airflow,
        "airflow.decorators": decorators,
        "airflow.hooks": hooks,
        "airflow.hooks.base": base,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    path = Path(__file__).resolve().parents[1] / "dags/gridskew_elexon_bmunits_dag.py"
    return runpy.run_path(str(path)), connections


def test_s4_dbt_uses_dev_schema_target(dag_helpers, monkeypatch):
    helpers, _ = dag_helpers
    env = helpers["dbt_environment"]("/tmp/bmunits")
    assert env["GRIDSKEW_DBT_NAME"] == "gridskew_prod"
    assert env["GRIDSKEW_DBT_USER"] == "gridskew_dbt"
    assert env["GRIDSKEW_DB_USER"] == "raw_writer"

    run = Mock(
        return_value=SimpleNamespace(stdout="", stderr="", check_returncode=Mock())
    )
    monkeypatch.setattr(helpers["subprocess"], "run", run)
    helpers["run_dbt"](env, "seed", "--select", "elexon_fuel_codes")
    command = run.call_args.args[0]
    assert command[command.index("--target") + 1] == "dev"
    assert command[:2] == ["dbt", "seed"]


@pytest.mark.parametrize("field,value", [("host", "other"), ("schema", "gridskew_dev")])
def test_s4_rejects_dbt_connection_outside_production(dag_helpers, field, value):
    helpers, connections = dag_helpers
    setattr(connections["gridskew_dbt"], field, value)
    with pytest.raises(RuntimeError, match="production database"):
        helpers["dbt_environment"]("/tmp/bmunits")
