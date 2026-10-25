"""A forecast run stores a poll only in the half-hour slot it was scheduled for."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fakes import DECORATED_FUNCTIONS, FakeAirflowSkipException, load_dag_file

SCHEDULED_AT = datetime(2026, 9, 30, 12, 5, tzinfo=timezone.utc)
EARLIER_SLOT_SCHEDULED_AT = datetime(2026, 9, 30, 11, 35, tzinfo=timezone.utc)
TASK_CLOCK = datetime(2026, 9, 30, 12, 7, 30, tzinfo=timezone.utc)
SCHEDULED_RUN = SimpleNamespace(run_type="scheduled")
MANUAL_RUN = SimpleNamespace(run_type="manual")


def stop_the_task_clock_at(monkeypatch, task, moment):
    """Make datetime.now() in the DAG file return `moment` when `task` runs."""

    clock = MagicMock()
    clock.now.return_value = moment
    monkeypatch.setitem(task.__globals__, "datetime", clock)


@pytest.fixture
def poll_forecast(monkeypatch):
    load_dag_file(monkeypatch, "gridskew_carbon_intensity_forecast_dag.py")
    DECORATED_FUNCTIONS["gridskew_carbon_intensity_forecast"]()
    task = DECORATED_FUNCTIONS["poll_forecast"]
    stop_the_task_clock_at(monkeypatch, task, TASK_CLOCK)
    return task


@pytest.fixture
def forecast_dag(monkeypatch):
    return load_dag_file(monkeypatch, "gridskew_carbon_intensity_forecast_dag.py")


def test_a_run_still_in_its_own_slot_is_on_time(forecast_dag):
    last_second_of_the_slot = datetime(2026, 9, 30, 12, 29, 59, tzinfo=timezone.utc)

    assert not forecast_dag["is_past_its_slot"](SCHEDULED_AT, last_second_of_the_slot)


def test_a_run_in_the_next_slot_is_past_its_slot(forecast_dag):
    first_second_of_the_next_slot = datetime(2026, 9, 30, 12, 30, tzinfo=timezone.utc)

    assert forecast_dag["is_past_its_slot"](SCHEDULED_AT, first_second_of_the_next_slot)


def test_a_cleared_run_from_an_earlier_slot_is_skipped_before_polling(poll_forecast):
    # No connection is configured, so reaching the poll would raise KeyError.
    with pytest.raises(FakeAirflowSkipException, match="slot"):
        poll_forecast(
            dag_run=SCHEDULED_RUN, data_interval_end=EARLIER_SLOT_SCHEDULED_AT
        )


def test_a_manual_run_in_the_current_slot_is_skipped_before_polling(poll_forecast):
    with pytest.raises(FakeAirflowSkipException, match="scheduled"):
        poll_forecast(dag_run=MANUAL_RUN, data_interval_end=SCHEDULED_AT)


def test_a_scheduled_run_in_its_slot_goes_on_to_poll(poll_forecast):
    # Reaching the connection (none is configured here) shows the guard let
    # the run through.
    with pytest.raises(KeyError, match="gridskew_prod"):
        poll_forecast(dag_run=SCHEDULED_RUN, data_interval_end=SCHEDULED_AT)
