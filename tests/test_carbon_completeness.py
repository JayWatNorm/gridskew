from datetime import datetime, timedelta, timezone

import pytest

from ingestion.carbon_intensity.completeness import (
    validate_forecast_window,
    validate_outturn_window,
)

PERIOD = timedelta(minutes=30)
START = datetime(2026, 8, 17, 7, 30, tzinfo=timezone.utc)


def make_period_rows(start, count):
    rows = []
    for index in range(count):
        period_start = start + index * PERIOD
        rows.append(
            {
                "from": period_start.strftime("%Y-%m-%dT%H:%MZ"),
                "to": (period_start + PERIOD).strftime("%Y-%m-%dT%H:%MZ"),
            }
        )
    return rows


@pytest.mark.parametrize("period_count", [96, 97])
def test_forecast_accepts_a_complete_48_hour_window(period_count):
    rows = make_period_rows(START, period_count)
    requested_at = START + timedelta(minutes=5)

    validate_forecast_window(rows, requested_at)


def test_forecast_rejects_a_request_outside_the_first_period():
    rows = make_period_rows(START, 96)
    requested_at = START + PERIOD

    with pytest.raises(ValueError, match="first period"):
        validate_forecast_window(rows, requested_at)


def test_forecast_rejects_a_window_shorter_than_48_hours():
    rows = make_period_rows(START, 95)

    with pytest.raises(ValueError, match="shorter than 48 hours"):
        validate_forecast_window(rows, START)


def test_forecast_rejects_a_gap_between_periods():
    rows = make_period_rows(START, 96)
    del rows[30]

    with pytest.raises(ValueError, match="period gap"):
        validate_forecast_window(rows, START)


def test_forecast_rejects_a_duplicate_period():
    rows = make_period_rows(START, 96)
    rows.insert(30, rows[30].copy())

    with pytest.raises(ValueError, match="duplicate period"):
        validate_forecast_window(rows, START)


def test_forecast_rejects_a_period_with_the_wrong_duration():
    rows = make_period_rows(START, 96)
    rows[30]["to"] = (START + 31 * PERIOD + timedelta(minutes=1)).strftime(
        "%Y-%m-%dT%H:%MZ"
    )

    with pytest.raises(ValueError, match="periods must be 30 minutes"):
        validate_forecast_window(rows, START)


@pytest.mark.parametrize(
    ("request_start", "first_period_start"),
    [
        pytest.param(
            datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc),
            datetime(2026, 8, 20, 11, 30, tzinfo=timezone.utc),
            id="request-on-boundary",
        ),
        pytest.param(
            datetime(2026, 8, 20, 12, 34, tzinfo=timezone.utc),
            datetime(2026, 8, 20, 12, 30, tzinfo=timezone.utc),
            id="request-inside-period",
        ),
    ],
)
def test_outturn_accepts_its_observed_inclusive_window(
    request_start,
    first_period_start,
):
    request_end = request_start + timedelta(days=7)
    rows = make_period_rows(first_period_start, 337)

    validate_outturn_window(rows, request_start, request_end)


@pytest.mark.parametrize(
    "missing_period",
    [
        pytest.param(0, id="first"),
        pytest.param(100, id="interior"),
        pytest.param(336, id="last"),
    ],
)
def test_outturn_rejects_a_missing_period(missing_period):
    request_start = datetime(2026, 8, 20, tzinfo=timezone.utc)
    request_end = request_start + timedelta(days=7)
    rows = make_period_rows(request_start - PERIOD, 337)
    del rows[missing_period]

    with pytest.raises(ValueError):
        validate_outturn_window(rows, request_start, request_end)
