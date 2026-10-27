"""Guard which settlement date each daily B1610 run captures, without Airflow."""

from datetime import date, datetime, timedelta, timezone

import pytest

from tests.fakes import (
    DAG_SETTINGS,
    MULTI_DAY_OUTAGE,
    load_dag_file,
    longest_retry_span,
    shortest_retry_span,
)

DAILY_CAPTURES = [
    pytest.param("gridskew_elexon_b1610_II_dag.py", "II", id="II"),
    pytest.param("gridskew_elexon_b1610_SF_dag.py", "SF", id="SF"),
    pytest.param("gridskew_elexon_b1610_RF_dag.py", "RF", id="RF"),
]

# Days after the settlement date, from docs/sources/elexon/020_b1610.md and
# 021_b1610_ingestion.md. II publishes after five working days: up to twelve
# calendar days around Christmas.
DAYS_UNTIL_PUBLISHED = [
    pytest.param("gridskew_elexon_b1610_II_dag.py", "II", 12, id="II"),
    pytest.param("gridskew_elexon_b1610_SF_dag.py", "SF", 28, id="SF"),
    pytest.param("gridskew_elexon_b1610_RF_dag.py", "RF", 420, id="RF"),
]
# RF is never replaced, so it has no end to its window.
DAYS_UNTIL_REPLACED = [
    pytest.param("gridskew_elexon_b1610_II_dag.py", "II", 21, id="II"),
    pytest.param("gridskew_elexon_b1610_SF_dag.py", "SF", 42, id="SF"),
]


def retry_policy_of(monkeypatch, file_name, run_type):
    load_dag_file(monkeypatch, file_name)
    return DAG_SETTINGS[f"gridskew_elexon_b1610_{run_type}"]["default_args"]


@pytest.mark.parametrize("file_name,run_type", DAILY_CAPTURES)
def test_daily_capture_asks_for_the_run_it_is_named_for(
    monkeypatch, file_name, run_type
):
    daily_dag = load_dag_file(monkeypatch, file_name)

    assert daily_dag["RUN_TYPE"] == run_type


@pytest.mark.parametrize("file_name,run_type", DAILY_CAPTURES)
def test_consecutive_daily_runs_capture_consecutive_settlement_dates(
    monkeypatch, file_name, run_type
):
    daily_dag = load_dag_file(monkeypatch, file_name)
    settlement_date_to_capture = daily_dag["settlement_date_to_capture"]
    first_interval_start = datetime(2026, 10, 24, tzinfo=timezone.utc)
    next_interval_start = first_interval_start + timedelta(days=1)

    first_date = settlement_date_to_capture(first_interval_start)

    assert first_date == date(2026, 10, 24) - daily_dag["LAG"]
    assert settlement_date_to_capture(next_interval_start) == first_date + timedelta(
        days=1
    )


@pytest.mark.parametrize(
    "file_name,run_type,days_until_published", DAYS_UNTIL_PUBLISHED
)
def test_daily_capture_first_asks_after_its_run_is_published(
    monkeypatch, file_name, run_type, days_until_published
):
    daily_dag = load_dag_file(monkeypatch, file_name)

    assert daily_dag["LAG"] >= timedelta(days=days_until_published)


@pytest.mark.parametrize("file_name,run_type", DAILY_CAPTURES)
def test_daily_capture_retries_through_a_multi_day_outage(
    monkeypatch, file_name, run_type
):
    retry_policy = retry_policy_of(monkeypatch, file_name, run_type)

    assert shortest_retry_span(retry_policy) >= MULTI_DAY_OUTAGE


@pytest.mark.parametrize("file_name,run_type,days_until_replaced", DAYS_UNTIL_REPLACED)
def test_daily_capture_stops_retrying_before_its_run_is_replaced(
    monkeypatch, file_name, run_type, days_until_replaced
):
    daily_dag = load_dag_file(monkeypatch, file_name)
    retry_policy = retry_policy_of(monkeypatch, file_name, run_type)

    last_try_ends = daily_dag["LAG"] + longest_retry_span(retry_policy)

    assert last_try_ends <= timedelta(days=days_until_replaced)


@pytest.mark.parametrize("file_name,run_type", DAILY_CAPTURES)
def test_dates_missed_during_an_outage_are_scheduled_afterwards(
    monkeypatch, file_name, run_type
):
    load_dag_file(monkeypatch, file_name)

    assert DAG_SETTINGS[f"gridskew_elexon_b1610_{run_type}"]["catchup"] is True


@pytest.mark.parametrize("file_name,run_type", DAILY_CAPTURES)
def test_a_retrying_date_does_not_hold_up_the_next_date(
    monkeypatch, file_name, run_type
):
    load_dag_file(monkeypatch, file_name)

    assert DAG_SETTINGS[f"gridskew_elexon_b1610_{run_type}"]["max_active_runs"] >= 2


def test_rf_history_starts_with_the_first_b1610_settlement_date(monkeypatch):
    rf_dag = load_dag_file(monkeypatch, "gridskew_elexon_b1610_RF_dag.py")
    settings = DAG_SETTINGS["gridskew_elexon_b1610_RF"]

    assert rf_dag["settlement_date_to_capture"](settings["start_date"]) == date(
        2025, 8, 22
    )
    # The release controller refuses a new DAG that would start unpaused.
    assert settings["is_paused_upon_creation"] is True
