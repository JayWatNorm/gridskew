"""Guard the nightly S6 dbt commands without requiring Airflow to run."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fakes import connection_returning, load_dag_file


@pytest.fixture
def nightly(monkeypatch):
    connection = SimpleNamespace(
        host="postgres",
        port=5432,
        schema="gridskew_prod",
        login="gridskew_dbt",
        password="test-only",
    )
    connections = {"gridskew_prod": connection, "gridskew_dbt": connection}
    return load_dag_file(monkeypatch, "gridskew__dbt_nightly_dag.py", connections)


def test_scheduled_run_writes_tables_only_after_the_run_code_test(nightly):
    commands = nightly["dbt_commands"](full_refresh=False)
    assert commands == [
        ["test", "--select", "stg_elexon__b1610,test_type:generic"],
        ["run", "--select", "int_elexon__b1610_period", "int_elexon__pn_period_mwh"],
        [
            "test",
            "--select",
            "int_elexon__b1610_period+",
            "int_elexon__pn_period_mwh+",
            "--exclude",
            "tag:full_population",
        ],
    ]
    # A scheduled run never rebuilds views or selects upstream models.
    for command in commands:
        assert "build" not in command
        assert "--full-refresh" not in command


def test_full_refresh_rebuilds_descendants_after_contract_tests(nightly):
    commands = nightly["dbt_commands"](full_refresh=True)
    assert commands[0] == ["test", "--select", "stg_elexon__b1610,test_type:generic"]
    assert commands[-1] == [
        "build",
        "--select",
        "int_elexon__b1610_period+",
        "int_elexon__pn_period_mwh+",
        "--full-refresh",
    ]


def test_missing_period_table_is_reported(nightly):
    conn = connection_returning((None, "dbt_dev.int_elexon__pn_period_mwh"))
    assert nightly["missing_period_tables"](conn) == ["int_elexon__b1610_period"]


def test_dbt_runs_in_the_dev_target_with_its_own_logs(nightly, monkeypatch):
    env = nightly["dbt_environment"]("/tmp/nightly")
    assert env["GRIDSKEW_DBT_USER"] == "gridskew_dbt"
    assert env["DBT_TARGET_PATH"] == "/tmp/nightly/target"
    assert "/dbt_logs/nightly/" in env["DBT_LOG_PATH"]

    finished_process = SimpleNamespace(stdout="", stderr="", check_returncode=Mock())
    run = Mock(return_value=finished_process)
    monkeypatch.setattr(nightly["subprocess"], "run", run)
    nightly["run_dbt"](env, ["run", "--select", "x"], timeout_minutes=30)
    command = run.call_args.args[0]
    assert command[command.index("--target") + 1] == "dev"
    assert run.call_args.kwargs["timeout"] == 30 * 60
