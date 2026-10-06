"""Guard which settlement date each daily B1610 run captures, without Airflow."""

from datetime import date, datetime, timedelta, timezone

import pytest
from fakes import load_dag_file

DAILY_CAPTURES = [
    pytest.param("gridskew_elexon_b1610_II_dag.py", "II", 14, id="II"),
    pytest.param("gridskew_elexon_b1610_SF_dag.py", "SF", 35, id="SF"),
]


@pytest.mark.parametrize("file_name,run_type,lag_days", DAILY_CAPTURES)
def test_daily_capture_names_its_run_and_offset(
    monkeypatch, file_name, run_type, lag_days
):
    daily_dag = load_dag_file(monkeypatch, file_name)

    assert daily_dag["RUN_TYPE"] == run_type
    assert daily_dag["LAG"] == timedelta(days=lag_days)


@pytest.mark.parametrize("file_name,run_type,lag_days", DAILY_CAPTURES)
def test_consecutive_daily_runs_capture_consecutive_settlement_dates(
    monkeypatch, file_name, run_type, lag_days
):
    daily_dag = load_dag_file(monkeypatch, file_name)
    settlement_date_to_capture = daily_dag["settlement_date_to_capture"]
    first_interval_start = datetime(2026, 10, 24, tzinfo=timezone.utc)
    next_interval_start = first_interval_start + timedelta(days=1)

    first_date = settlement_date_to_capture(first_interval_start)

    assert first_date == date(2026, 10, 24) - timedelta(days=lag_days)
    assert settlement_date_to_capture(next_interval_start) == first_date + timedelta(
        days=1
    )
