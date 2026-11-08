"""Guard when the REMIT re-read DAG runs and which days it hands the poller,
without Airflow."""

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tests.fakes import (
    DAG_SETTINGS,
    DECORATED_FUNCTIONS,
    TASK_SETTINGS,
    FakeCronDataIntervalTimetable,
    load_dag_file,
)

DAG_FILE = "gridskew_elexon_remit_reread_dag.py"
DAG_ID = "gridskew_elexon_remit_reread"
TASK_NAME = "reread_remit"
GRIDSKEW_PROD = SimpleNamespace(
    host="db", port=5432, schema="gridskew_prod", login="u", password="p"
)
NOVEMBER_9_RUN_INTERVAL_START = datetime(2026, 11, 8, 4, 40, tzinfo=timezone.utc)
# The same instant written twelve hours behind UTC, on the previous day.
NOVEMBER_9_RUN_INTERVAL_START_AT_UTC_MINUS_12 = datetime(
    2026, 11, 7, 16, 40, tzinfo=timezone(timedelta(hours=-12))
)


def load_reread_dag(monkeypatch):
    """Load the DAG file and run its body, so its task is defined."""

    names = load_dag_file(monkeypatch, DAG_FILE, {"gridskew_prod": GRIDSKEW_PROD})
    DECORATED_FUNCTIONS[DAG_ID]()
    return names


def test_the_dag_runs_at_04_40_utc_with_a_full_day_data_interval(monkeypatch):
    load_reread_dag(monkeypatch)

    schedule = DAG_SETTINGS[DAG_ID]["schedule"]

    assert isinstance(schedule, FakeCronDataIntervalTimetable)
    assert schedule.cron == "40 4 * * *"
    assert schedule.timezone == "UTC"


def test_one_run_at_a_time_without_catch_up_created_paused(monkeypatch):
    load_reread_dag(monkeypatch)
    settings = DAG_SETTINGS[DAG_ID]

    # Each run covers seven days, so a missed morning needs no catch-up run.
    assert settings["catchup"] is False
    assert settings["max_active_runs"] == 1
    # The release controller refuses a new DAG that would start unpaused.
    assert settings["is_paused_upon_creation"] is True


def test_the_start_date_is_on_the_schedule_and_before_any_release(monkeypatch):
    load_reread_dag(monkeypatch)

    assert DAG_SETTINGS[DAG_ID]["start_date"] == datetime(
        2026, 11, 1, 4, 40, tzinfo=timezone.utc
    )


def test_the_dag_is_tagged_gridskew_and_elexon_only(monkeypatch):
    load_reread_dag(monkeypatch)

    # The release controller refuses a dbt tag on a DAG that runs no dbt.
    assert DAG_SETTINGS[DAG_ID]["tags"] == ["gridskew", "elexon"]


def test_the_task_takes_the_elexon_slot_once_for_at_most_15_minutes(monkeypatch):
    load_reread_dag(monkeypatch)
    settings = TASK_SETTINGS[TASK_NAME]

    assert settings["pool"] == "elexon"
    # The next morning's run is its retry.
    assert settings["retries"] == 0
    assert settings["execution_timeout"] == timedelta(minutes=15)


@pytest.mark.parametrize(
    "interval_start",
    [
        pytest.param(NOVEMBER_9_RUN_INTERVAL_START, id="UTC"),
        pytest.param(NOVEMBER_9_RUN_INTERVAL_START_AT_UTC_MINUS_12, id="UTC-12"),
    ],
)
def test_the_last_day_is_the_utc_day_the_interval_starts(monkeypatch, interval_start):
    reread_dag = load_reread_dag(monkeypatch)

    assert reread_dag["last_reread_day"](interval_start) == date(2026, 11, 8)


def test_the_task_rereads_up_to_its_last_day_on_gridskew_prod(monkeypatch):
    load_reread_dag(monkeypatch)

    with (
        patch("ingestion.database.connect") as connect,
        patch("ingestion.elexon.remit_poller.reread") as reread,
    ):
        DECORATED_FUNCTIONS[TASK_NAME](
            data_interval_start=NOVEMBER_9_RUN_INTERVAL_START
        )

    reread.assert_called_once_with(connect.return_value, date(2026, 11, 8))
    connect.return_value.close.assert_called_once_with()
