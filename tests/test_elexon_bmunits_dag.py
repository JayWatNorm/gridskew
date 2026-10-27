"""Guard the deployed BM-unit dbt target without requiring Airflow to run."""

import re
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

from tests.fakes import load_dag_file

RUN_CLOCK = datetime(2026, 10, 12, 0, 4, 31, tzinfo=timezone.utc)
DBT_SETTINGS = (
    "DBT_TARGET_PATH",
    "DBT_PACKAGES_INSTALL_PATH",
    "DBT_LOG_PATH",
    "GRIDSKEW_DBT_HOST",
    "GRIDSKEW_DBT_PORT",
    "GRIDSKEW_DBT_USER",
    "GRIDSKEW_DBT_PASSWORD",
    "GRIDSKEW_DBT_NAME",
)
# The raw writer's connection, as the platform profile's prod target reads it.
RAW_CONNECTION_SETTINGS = (
    "GRIDSKEW_DB_HOST",
    "GRIDSKEW_DB_PORT",
    "GRIDSKEW_DB_USER",
    "GRIDSKEW_DB_PASSWORD",
    "GRIDSKEW_DB_NAME",
)


@pytest.fixture
def connections():
    return {
        # No port: the default 5432 must still match the dbt connection's.
        "gridskew_prod": SimpleNamespace(
            host="postgres",
            port=None,
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


def stop_the_clock_at(monkeypatch, function, moment):
    """Make datetime.now() in the DAG file return `moment` when `function` runs."""

    clock = MagicMock()
    clock.now.return_value = moment
    monkeypatch.setitem(function.__globals__, "datetime", clock)


def dbt_settings(env):
    """The DBT_* and GRIDSKEW_DBT_* variables the dbt environment sets."""

    settings = {}
    for name in DBT_SETTINGS:
        settings[name] = env[name]
    return settings


def raw_connection_settings(env):
    """Every GRIDSKEW_DB_* variable in the environment, by name."""

    names = []
    for name in sorted(env):
        if name.startswith("GRIDSKEW_DB_"):
            names.append(name)
    return names


def test_dbt_runs_as_the_dbt_role_in_the_dev_target(dag_helpers, monkeypatch):
    bmunits_dbt_environment = dag_helpers["bmunits_dbt_environment"]
    stop_the_clock_at(monkeypatch, bmunits_dbt_environment, RUN_CLOCK)
    env = bmunits_dbt_environment("/tmp/bmunits", "scheduled__2026-10-12")
    assert dbt_settings(env) == {
        "DBT_TARGET_PATH": "/tmp/bmunits/target",
        "DBT_PACKAGES_INSTALL_PATH": "/tmp/bmunits/packages",
        "DBT_LOG_PATH": (
            "/opt/airflow/data/gridskew/dbt_logs/bmunits/2026-10-12"
            "/scheduled__2026-10-12"
        ),
        "GRIDSKEW_DBT_HOST": "postgres",
        "GRIDSKEW_DBT_PORT": "5432",
        "GRIDSKEW_DBT_USER": "gridskew_dbt",
        "GRIDSKEW_DBT_PASSWORD": "test-only",
        "GRIDSKEW_DBT_NAME": "gridskew_prod",
    }

    finished_process = SimpleNamespace(stdout="", stderr="", check_returncode=Mock())
    run = Mock(return_value=finished_process)
    monkeypatch.setattr(dag_helpers["subprocess"], "run", run)
    dag_helpers["run_dbt"](env, "seed", "--select", "elexon_fuel_codes")
    command = run.call_args.args[0]
    assert command[command.index("--target") + 1] == "dev"
    assert command[command.index("--warn-error-options") + 1] == (
        '{"error": ["NoNodesForSelectionCriteria"]}'
    )


def test_dbt_is_not_given_the_raw_writer_connection(dag_helpers, monkeypatch):
    for name in RAW_CONNECTION_SETTINGS:
        monkeypatch.delenv(name, raising=False)

    env = dag_helpers["bmunits_dbt_environment"](
        "/tmp/bmunits", "scheduled__2026-10-12"
    )

    assert raw_connection_settings(env) == []


def test_two_runs_log_to_two_folders(dag_helpers):
    scheduled = dag_helpers["bmunits_dbt_environment"](
        "/tmp/bmunits", "scheduled__2026-10-12"
    )
    manual = dag_helpers["bmunits_dbt_environment"](
        "/tmp/bmunits", "manual__2026-10-12"
    )

    assert scheduled["DBT_LOG_PATH"] != manual["DBT_LOG_PATH"]
    # The nightly DAG prunes the day folders under bmunits/.
    assert re.fullmatch(
        r"/opt/airflow/data/gridskew/dbt_logs/bmunits/\d{4}-\d{2}-\d{2}"
        r"/scheduled__2026-10-12",
        scheduled["DBT_LOG_PATH"],
    )


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
        dag_helpers["bmunits_dbt_environment"]("/tmp/bmunits", "manual__1")


def test_rejects_connections_that_name_no_database(dag_helpers, connections):
    connections["gridskew_prod"].schema = None
    connections["gridskew_dbt"].schema = None
    with pytest.raises(RuntimeError, match="production database"):
        dag_helpers["bmunits_dbt_environment"]("/tmp/bmunits", "manual__1")
