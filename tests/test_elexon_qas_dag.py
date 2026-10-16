"""Guard what the QAS DAG captures and when, without Airflow."""

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fakes import (
    DAG_SETTINGS,
    DECORATED_FUNCTIONS,
    FakeAirflowSkipException,
    load_dag_file,
)
from test_elexon_b1610_daily_dags import MULTI_DAY_OUTAGE, shortest_retry_span

from ingestion.elexon.qas_backfill import BACKFILL_LAST_DATE, FIRST_QAS_DATE

DAG_FILE = "gridskew_elexon_qas_dag.py"
DAG_ID = "gridskew_elexon_qas"
CAPTURES = [
    pytest.param("capture_14_days", 14, id="14"),
    pytest.param("capture_35_days", 35, id="35"),
    pytest.param("capture_450_days", 450, id="450"),
]


def load_tasks(monkeypatch):
    connection = SimpleNamespace(
        host="db", port=5432, schema="gridskew_prod", login="u", password="p"
    )
    names = load_dag_file(monkeypatch, DAG_FILE, {"gridskew_prod": connection})
    DECORATED_FUNCTIONS[DAG_ID]()
    return names


def captured_date(monkeypatch, task_name, data_interval_start):
    """Run one task; return the settlement date it captured."""

    load_tasks(monkeypatch)
    with (
        patch("ingestion.database.connect"),
        patch("ingestion.elexon.qas_poller.run") as mock_run,
    ):
        DECORATED_FUNCTIONS[task_name](data_interval_start=data_interval_start)
    (_conn, settlement_date) = mock_run.call_args.args
    return settlement_date


@pytest.mark.parametrize("task_name,lag_days", CAPTURES)
def test_each_task_captures_its_lag_and_consecutive_runs_consecutive_dates(
    monkeypatch, task_name, lag_days
):
    first_interval_start = datetime(2026, 12, 1, tzinfo=timezone.utc)
    next_interval_start = first_interval_start + timedelta(days=1)

    first_date = captured_date(monkeypatch, task_name, first_interval_start)
    next_date = captured_date(monkeypatch, task_name, next_interval_start)

    assert first_date == date(2026, 12, 1) - timedelta(days=lag_days)
    assert next_date == first_date + timedelta(days=1)


def test_a_target_on_the_first_qas_date_is_captured(monkeypatch):
    interval_start = datetime(2026, 11, 15, tzinfo=timezone.utc)

    assert captured_date(monkeypatch, "capture_450_days", interval_start) == (
        FIRST_QAS_DATE
    )


def test_a_target_before_the_first_qas_date_is_skipped(monkeypatch):
    load_tasks(monkeypatch)
    interval_start = datetime(2026, 11, 14, tzinfo=timezone.utc)

    with patch("ingestion.elexon.qas_poller.run") as mock_run:
        with pytest.raises(FakeAirflowSkipException, match="2025-08-21"):
            DECORATED_FUNCTIONS["capture_450_days"](data_interval_start=interval_start)

    mock_run.assert_not_called()


def test_the_first_run_takes_over_the_day_after_the_backfill(monkeypatch):
    load_tasks(monkeypatch)
    start_date = DAG_SETTINGS[DAG_ID]["start_date"]

    first_target = captured_date(monkeypatch, "capture_14_days", start_date)

    assert first_target == BACKFILL_LAST_DATE + timedelta(days=1)


def test_a_capture_retries_through_a_multi_day_outage(monkeypatch):
    load_tasks(monkeypatch)

    retry_policy = DAG_SETTINGS[DAG_ID]["default_args"]

    assert shortest_retry_span(retry_policy) >= MULTI_DAY_OUTAGE


def test_missed_dates_are_caught_up_and_a_retrying_date_holds_up_no_other(
    monkeypatch,
):
    load_tasks(monkeypatch)
    settings = DAG_SETTINGS[DAG_ID]

    assert settings["catchup"] is True
    assert settings["max_active_runs"] >= 2
    # The release controller refuses a new DAG that would start unpaused.
    assert settings["is_paused_upon_creation"] is True
