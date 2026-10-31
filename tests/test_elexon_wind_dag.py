"""Guard what the wind DAG captures and when, without Airflow."""

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import requests

from ingestion.elexon import wind_poller
from tests.fakes import (
    DAG_SETTINGS,
    DECORATED_FUNCTIONS,
    TASK_SETTINGS,
    FakeCronDataIntervalTimetable,
    cursor_of,
    load_dag_file,
    longest_retry_span,
    waits_before_each_retry,
)

DAG_FILE = "gridskew_elexon_wind_dag.py"
DAG_ID = "gridskew_elexon_wind"
GRIDSKEW_PROD = SimpleNamespace(
    host="db", port=5432, schema="gridskew_prod", login="u", password="p"
)
TARGET_TASKS = ["poll_windfor", "poll_fuelhh"]
LOOK_BACK_TASKS = ["poll_windfor_look_back", "poll_fuelhh_look_back"]
TASK_CAPTURES = [
    pytest.param("poll_windfor", "capture_windfor_target", id="poll_windfor"),
    pytest.param(
        "poll_windfor_look_back",
        "capture_windfor_look_back",
        id="poll_windfor_look_back",
    ),
    pytest.param("poll_fuelhh", "capture_fuelhh_target", id="poll_fuelhh"),
    pytest.param(
        "poll_fuelhh_look_back", "capture_fuelhh_look_back", id="poll_fuelhh_look_back"
    ),
]
DECEMBER_1_RUN_INTERVAL_START = datetime(2026, 12, 1, 6, 0, tzinfo=timezone.utc)
# The same instant written twelve hours behind UTC, on the previous day.
DECEMBER_1_RUN_INTERVAL_START_AT_UTC_MINUS_12 = datetime(
    2026, 11, 30, 18, 0, tzinfo=timezone(timedelta(hours=-12))
)


def load_wind_dag(monkeypatch):
    """Load the DAG file and run its body, so its tasks are defined."""

    names = load_dag_file(monkeypatch, DAG_FILE, {"gridskew_prod": GRIDSKEW_PROD})
    DECORATED_FUNCTIONS[DAG_ID]()
    return names


def unavailable(url, params, headers, timeout):
    """Stand in for an Elexon that answers every request with HTTP 503."""

    response = MagicMock()
    response.status_code = 503
    response.raise_for_status.side_effect = requests.HTTPError(
        "503 Service Unavailable", response=response
    )
    return response


def nothing_stored():
    """A fake connection whose completeness and quarantine queries find no row."""

    conn = MagicMock()
    cursor_of(conn).fetchall.return_value = []
    return conn


def tries(retries):
    return retries + 1


def test_the_dag_runs_at_06_00_utc_with_a_full_day_data_interval(monkeypatch):
    load_wind_dag(monkeypatch)

    schedule = DAG_SETTINGS[DAG_ID]["schedule"]

    assert isinstance(schedule, FakeCronDataIntervalTimetable)
    assert schedule.cron == "0 6 * * *"
    assert schedule.timezone == "UTC"


def test_one_run_at_a_time_without_catch_up_created_paused(monkeypatch):
    load_wind_dag(monkeypatch)
    settings = DAG_SETTINGS[DAG_ID]

    # The look-back asks for missed days, so the scheduler need not.
    assert settings["catchup"] is False
    assert settings["max_active_runs"] == 1
    # The release controller refuses a new DAG that would start unpaused.
    assert settings["is_paused_upon_creation"] is True


def test_the_first_run_targets_the_first_capture_day(monkeypatch):
    wind_dag = load_wind_dag(monkeypatch)

    first_target = wind_dag["target_day"](DAG_SETTINGS[DAG_ID]["start_date"])

    assert first_target == wind_poller.FIRST_CAPTURE_DAY


@pytest.mark.parametrize(
    "interval_start",
    [
        pytest.param(DECEMBER_1_RUN_INTERVAL_START, id="UTC"),
        pytest.param(DECEMBER_1_RUN_INTERVAL_START_AT_UTC_MINUS_12, id="UTC-12"),
    ],
)
def test_a_run_targets_the_utc_day_its_interval_starts(monkeypatch, interval_start):
    wind_dag = load_wind_dag(monkeypatch)

    assert wind_dag["target_day"](interval_start) == date(2026, 12, 1)


def test_the_dag_is_tagged_gridskew_and_elexon_only(monkeypatch):
    load_wind_dag(monkeypatch)

    # The release controller refuses a dbt tag on a DAG that runs no dbt.
    assert DAG_SETTINGS[DAG_ID]["tags"] == ["gridskew", "elexon"]


@pytest.mark.parametrize("task_name", TARGET_TASKS + LOOK_BACK_TASKS)
def test_every_task_takes_the_one_elexon_pool_slot(monkeypatch, task_name):
    load_wind_dag(monkeypatch)

    assert TASK_SETTINGS[task_name]["pool"] == "elexon"


@pytest.mark.parametrize("task_name", LOOK_BACK_TASKS)
def test_a_look_back_task_tries_once_for_at_most_20_minutes(monkeypatch, task_name):
    load_wind_dag(monkeypatch)
    settings = TASK_SETTINGS[task_name]

    # The next morning's run is its retry.
    assert settings["retries"] == 0
    assert settings["execution_timeout"] == timedelta(minutes=20)


def test_a_target_retry_waits_at_least_30_minutes_and_ends_within_a_day(
    monkeypatch,
):
    load_wind_dag(monkeypatch)
    retry_policy = DAG_SETTINGS[DAG_ID]["default_args"]

    first_wait = waits_before_each_retry(retry_policy, jitter=1)[0]

    # Elexon's licence counts asking more than once a half hour as misuse.
    assert first_wait >= timedelta(minutes=30)
    # The next morning's run asks again.
    assert longest_retry_span(retry_policy) < timedelta(hours=24)


@pytest.mark.parametrize("task_name,capture_name", TASK_CAPTURES)
def test_each_task_captures_its_target_on_gridskew_prod(
    monkeypatch, task_name, capture_name
):
    load_wind_dag(monkeypatch)

    with (
        patch("ingestion.database.connect") as connect,
        patch(f"ingestion.elexon.wind_poller.{capture_name}") as capture,
    ):
        DECORATED_FUNCTIONS[task_name](
            data_interval_start=DECEMBER_1_RUN_INTERVAL_START
        )

    capture.assert_called_once_with(connect.return_value, date(2026, 12, 1))
    connect.return_value.close.assert_called_once_with()


@pytest.mark.parametrize(
    "target_capture,look_back_capture",
    [
        pytest.param(
            wind_poller.capture_windfor_target,
            wind_poller.capture_windfor_look_back,
            id="WINDFOR",
        ),
        pytest.param(
            wind_poller.capture_fuelhh_target,
            wind_poller.capture_fuelhh_look_back,
            id="FUELHH",
        ),
    ],
)
def test_a_run_asks_at_most_13_times_per_dataset_when_every_request_fails(
    monkeypatch, target_capture, look_back_capture
):
    load_wind_dag(monkeypatch)
    target_retries = DAG_SETTINGS[DAG_ID]["default_args"]["retries"]
    look_back_retries = TASK_SETTINGS["poll_windfor_look_back"]["retries"]
    target = date(2026, 11, 10)

    with (
        patch("ingestion.elexon.wind_poller.time.sleep"),
        patch(
            "ingestion.elexon.wind_poller.requests.get", side_effect=unavailable
        ) as get,
    ):
        for _try in range(tries(target_retries)):
            with pytest.raises(RuntimeError, match="HTTP 503"):
                target_capture(nothing_stored(), target)
        for _try in range(tries(look_back_retries)):
            with pytest.raises(RuntimeError, match="HTTP 503"):
                look_back_capture(nothing_stored(), target)

    # 6 tries of the target and 7 days before it, each asked once.
    assert get.call_count == 13
