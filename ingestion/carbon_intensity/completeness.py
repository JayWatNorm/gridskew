"""Semantic period-window checks for Carbon Intensity responses."""

from datetime import datetime, timedelta, timezone
from itertools import pairwise

PERIOD = timedelta(minutes=30)


def validate_forecast_window(rows, requested_at):
    """Require a continuous forecast covering 48 hours from its first period."""

    periods = _periods(rows, "forecast")
    first_start, first_end = periods[0]
    final_end = periods[-1][1]

    if not first_start <= requested_at < first_end:
        raise ValueError(
            "Incomplete Carbon Intensity forecast response: first period does not "
            "contain the request time"
        )
    if final_end < first_start + timedelta(hours=48):
        raise ValueError(
            "Incomplete Carbon Intensity forecast response: window is shorter "
            "than 48 hours"
        )


def validate_outturn_window(rows, request_start, request_end):
    """Require the inclusive half-hour coverage observed from the range endpoint."""

    periods = _periods(rows, "outturn")
    expected_first_start = _ceil_to_period(request_start) - PERIOD
    expected_final_end = _ceil_to_period(request_end)

    if periods[0][0] != expected_first_start or periods[-1][1] != expected_final_end:
        raise ValueError(
            "Incomplete Carbon Intensity outturn response: returned boundaries do "
            "not cover the inclusive request window"
        )


def _periods(rows, dataset):
    periods = []
    for row in rows:
        period_start = _timestamp(row["from"])
        period_end = _timestamp(row["to"])
        periods.append((period_start, period_end))
    periods.sort()

    if any(end - start != PERIOD for start, end in periods):
        raise ValueError(
            f"Incomplete Carbon Intensity {dataset} response: periods must be "
            "30 minutes"
        )

    starts = [start for start, _ in periods]
    if len(set(starts)) != len(starts):
        raise ValueError(
            f"Incomplete Carbon Intensity {dataset} response: duplicate period"
        )

    if any(current[1] != following[0] for current, following in pairwise(periods)):
        raise ValueError(f"Incomplete Carbon Intensity {dataset} response: period gap")

    return periods


def _timestamp(value):
    return datetime.strptime(value, "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)


def _ceil_to_period(value):
    value = value.replace(second=0, microsecond=0)
    if value.minute == 0 or value.minute == 30:
        return value
    if value.minute < 30:
        return value.replace(minute=30)
    return value.replace(minute=0) + timedelta(hours=1)
