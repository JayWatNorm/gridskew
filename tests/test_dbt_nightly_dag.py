"""Guard the nightly dbt commands without requiring Airflow to run."""

from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fakes import connection_returning, cursor_of, load_dag_file

FINISHED_AT = datetime(2026, 10, 9, 2, 41, tzinfo=timezone.utc)


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
def nightly(monkeypatch, connections):
    return load_dag_file(monkeypatch, "gridskew__dbt_nightly_dag.py", connections)


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
    nightly, connections, field, value, refusal
):
    setattr(connections["gridskew_dbt"], field, value)
    with pytest.raises(RuntimeError, match=refusal):
        nightly["dbt_environment"]("/tmp/nightly", "manual__1")


def test_rejects_connections_that_name_no_database(nightly, connections):
    connections["gridskew_prod"].schema = None
    connections["gridskew_dbt"].schema = None
    with pytest.raises(RuntimeError, match="production database"):
        nightly["dbt_environment"]("/tmp/nightly", "manual__1")


@pytest.fixture
def dbt_target(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    (target / "manifest.json").write_text("new manifest", encoding="utf-8")
    (target / "run_results.json").write_text("new results", encoding="utf-8")
    return target


def test_scheduled_run_writes_tables_only_after_the_run_code_test(nightly):
    commands = nightly["dbt_commands"](full_refresh=False)

    assert commands[0] == ["test", "--selector", "nightly_run_code_check"]
    verbs = []
    for command in commands:
        verbs.append(command[0])
    assert "run" in verbs[1:]


def test_the_missing_table_query_asks_for_every_period_table(nightly):
    three_tables = ("first_table", "second_table", "third_table")
    conn = connection_returning(("dbt_dev.first_table", None, "dbt_dev.third_table"))

    assert nightly["missing_period_tables"](conn, three_tables) == ["second_table"]

    _query, qualified_names = cursor_of(conn).execute.call_args.args
    assert qualified_names == [
        "dbt_dev.first_table",
        "dbt_dev.second_table",
        "dbt_dev.third_table",
    ]


def test_every_dbt_command_runs_in_dev_with_a_timeout_and_records_its_node_results(
    nightly, monkeypatch
):
    env = nightly["dbt_environment"]("/tmp/nightly", "scheduled__2026-10-09")
    assert env["GRIDSKEW_AIRFLOW_RUN_ID"] == "scheduled__2026-10-09"

    finished_process = SimpleNamespace(stdout="", stderr="", check_returncode=Mock())
    run = Mock(return_value=finished_process)
    monkeypatch.setattr(nightly["subprocess"], "run", run)
    nightly["run_dbt"](env, ["test", "--selector", "x"], timeout_minutes=30)
    command = run.call_args.args[0]
    assert command[command.index("--target") + 1] == "dev"
    assert command[command.index("--vars") + 1] == "{audit: true}"
    assert run.call_args.kwargs["timeout"] == 30 * 60


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


def test_logs_go_in_a_folder_per_day_and_run(nightly):
    folder = nightly["log_folder"]("scheduled__2026-10-09", date(2026, 10, 9))

    assert folder.endswith("/dbt_logs/nightly/2026-10-09/scheduled__2026-10-09")


def test_no_published_manifest_needs_no_full_refresh(nightly, monkeypatch, tmp_path):
    run = Mock()
    monkeypatch.setattr(nightly["subprocess"], "run", run)

    assert nightly["models_needing_full_refresh"]({}, tmp_path) == []
    run.assert_not_called()


def test_changed_models_below_a_period_table_need_a_full_refresh(
    nightly, monkeypatch, tmp_path
):
    (tmp_path / "manifest.json").write_text("{}")
    env = nightly["dbt_environment"]("/tmp/nightly", "scheduled__2026-10-09")
    listed = SimpleNamespace(
        stdout="int_shortfall_by_unit_period\nfct_generation\nfct_generation\n",
        stderr="",
        check_returncode=Mock(),
    )
    run = Mock(return_value=listed)
    monkeypatch.setattr(nightly["subprocess"], "run", run)

    changed = nightly["models_needing_full_refresh"](env, tmp_path)

    assert changed == ["fct_generation", "int_shortfall_by_unit_period"]
    command = run.call_args.args[0]
    assert command[command.index("--state") + 1] == str(tmp_path)
    assert "state:modified+,int_shortfall_by_unit_period+" in command
    assert "state:modified+,int_elexon__bm_unit_cohort+" in command
    # A new staging view above a period table is built by the full refresh only
    # when it is listed here.
    assert "state:modified+,stg_elexon__qas+" in command


def test_a_full_refresh_rebuilds_only_what_changed(nightly):
    changed = ["int_elexon__instruction_intervals", "int_shortfall_by_unit_period"]

    assert nightly["relations_to_rebuild"](changed, [], False) == tuple(changed)


@pytest.mark.parametrize(
    "changed,missing,rebuild_all",
    [
        pytest.param([], [], False, id="nothing-changed"),
        pytest.param(
            ["int_shortfall_by_unit_period"], ["x"], False, id="table-missing"
        ),
        pytest.param(["int_shortfall_by_unit_period"], [], True, id="rebuild-all"),
    ],
)
def test_a_full_refresh_rebuilds_everything_when_it_cannot_tell(
    nightly, changed, missing, rebuild_all
):
    everything = (*nightly["REFRESH_VIEWS"], *nightly["PERIOD_TABLES"])

    assert nightly["relations_to_rebuild"](changed, missing, rebuild_all) == everything


def test_a_full_refresh_builds_the_chosen_relations_and_below(nightly):
    commands = nightly["dbt_commands"](True, ("int_shortfall_by_unit_period",))

    assert commands[0] == ["test", "--selector", "nightly_run_code_check"]
    rebuild = commands[-1]
    assert rebuild[:3] == ["build", "--select", "int_shortfall_by_unit_period+"]
    assert rebuild[-1] == "--full-refresh"
