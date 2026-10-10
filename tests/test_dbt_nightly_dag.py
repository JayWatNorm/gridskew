"""Guard the nightly dbt commands without requiring Airflow to run."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fakes import connection_returning, cursor_of, load_dag_file

FINISHED_AT = datetime(2026, 10, 9, 2, 41, tzinfo=timezone.utc)


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


@pytest.fixture
def dbt_target(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    (target / "manifest.json").write_text("new manifest", encoding="utf-8")
    (target / "run_results.json").write_text("new results", encoding="utf-8")
    return target


def test_scheduled_run_writes_tables_only_after_the_run_code_test(nightly):
    commands = nightly["dbt_commands"](full_refresh=False)
    assert commands == [
        ["test", "--selector", "nightly_run_code_check"],
        ["run", "--selector", "nightly_models"],
        ["test", "--selector", "nightly_tests"],
    ]
    # A scheduled run never rebuilds views.
    for command in commands:
        assert "build" not in command
        assert "--full-refresh" not in command
        assert "-f" not in command


def test_full_refresh_rebuilds_descendants_after_contract_tests(nightly):
    commands = nightly["dbt_commands"](full_refresh=True)
    assert commands[0] == ["test", "--selector", "nightly_run_code_check"]
    assert commands[-1] == [
        "build",
        "--select",
        "int_elexon__bm_unit_cohort+",
        "int_elexon__b1610_period+",
        "int_elexon__pn_period_mwh+",
        "int_elexon__instruction_intervals+",
        "int_shortfall_by_unit_period+",
        "--full-refresh",
    ]


def test_missing_period_table_is_reported(nightly):
    conn = connection_returning(
        (
            None,
            "dbt_dev.int_elexon__pn_period_mwh",
            "dbt_dev.int_elexon__instruction_intervals",
            "dbt_dev.int_shortfall_by_unit_period",
        )
    )
    assert nightly["missing_period_tables"](conn) == ["int_elexon__b1610_period"]


def test_the_missing_table_query_asks_for_every_period_table(nightly):
    three_tables = ("first_table", "second_table", "third_table")
    conn = connection_returning(("dbt_dev.first_table", None, "dbt_dev.third_table"))

    assert nightly["missing_period_tables"](conn, three_tables) == ["second_table"]

    query, qualified_names = cursor_of(conn).execute.call_args.args
    assert query == "SELECT to_regclass(%s), to_regclass(%s), to_regclass(%s)"
    assert qualified_names == [
        "dbt_dev.first_table",
        "dbt_dev.second_table",
        "dbt_dev.third_table",
    ]


def test_dbt_runs_in_the_dev_target_with_its_own_logs(nightly, monkeypatch):
    env = nightly["dbt_environment"]("/tmp/nightly", "scheduled__2026-10-09")
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


def test_every_dbt_command_records_its_node_results(nightly, monkeypatch):
    env = nightly["dbt_environment"]("/tmp/nightly", "scheduled__2026-10-09")
    assert env["GRIDSKEW_AIRFLOW_RUN_ID"] == "scheduled__2026-10-09"

    finished_process = SimpleNamespace(stdout="", stderr="", check_returncode=Mock())
    run = Mock(return_value=finished_process)
    monkeypatch.setattr(nightly["subprocess"], "run", run)
    nightly["run_dbt"](env, ["test", "--selector", "x"], timeout_minutes=30)
    command = run.call_args.args[0]
    assert command[command.index("--vars") + 1] == "{audit: true}"


def test_artifacts_are_published_for_the_day_and_as_latest(nightly, dbt_target):
    artifacts = dbt_target.parent / "artifacts"
    latest = artifacts / "latest"
    latest.mkdir(parents=True)
    (latest / "manifest.json").write_text("old manifest", encoding="utf-8")

    nightly["publish_artifacts"](dbt_target, artifacts, FINISHED_AT)

    published_files = []
    for published in sorted(artifacts.rglob("*")):
        if published.is_file():
            published_files.append(
                (
                    published.relative_to(artifacts).as_posix(),
                    published.read_text(encoding="utf-8"),
                )
            )
    assert published_files == [
        ("2026-10-09/manifest.json", "new manifest"),
        ("2026-10-09/run_results.json", "new results"),
        ("latest/manifest.json", "new manifest"),
        ("latest/run_results.json", "new results"),
    ]


def test_only_day_folders_older_than_thirty_days_are_removed(nightly, dbt_target):
    artifacts = dbt_target.parent / "artifacts"
    for folder_name in ("2026-09-08", "2026-09-09", "not-a-day"):
        (artifacts / folder_name).mkdir(parents=True)

    nightly["publish_artifacts"](dbt_target, artifacts, FINISHED_AT)

    folder_names = []
    for folder in sorted(artifacts.iterdir()):
        folder_names.append(folder.name)
    assert folder_names == ["2026-09-09", "2026-10-09", "latest", "not-a-day"]
