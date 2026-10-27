import json
from datetime import datetime, timedelta, timezone
from itertools import pairwise
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import psycopg
import pytest

from ingestion.carbon_intensity.outturn_poller import (
    build_windows,
    floor_to_period,
    is_history_refetch_day,
    parse,
    process_window,
)
from ingestion.carbon_intensity.outturn_poller import run as run_poller
from ingestion.routing import ContractRejected
from tests.fakes import cursor_of

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "carbon_intensity" / "outturn.json"
RETRIEVED_AT = datetime(2026, 8, 19, 21, 43, 17, tzinfo=timezone.utc)
WINDOW_START = datetime(2025, 8, 20, tzinfo=timezone.utc)
WINDOW_END = datetime(2026, 8, 20, tzinfo=timezone.utc)
HISTORY_OLDER_THAN_THE_HORIZON = (
    100,
    datetime(2020, 1, 1, tzinfo=timezone.utc),
    datetime(2026, 8, 1, tzinfo=timezone.utc),
)


@pytest.fixture
def payload():
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def clock_reading(now):
    """Patch the poller's clock, so `run` reads `now`."""

    clock = Mock()
    clock.now.return_value = now
    return patch("ingestion.carbon_intensity.outturn_poller.datetime", clock)


def clock_reading_in_turn(*readings):
    """Patch the poller's clock, so each read of it returns the next reading."""

    clock = Mock()
    clock.now.side_effect = readings
    return patch("ingestion.carbon_intensity.outturn_poller.datetime", clock)


def a_period_start_100_days_before(now):
    this_hour = now.replace(minute=0, second=0, microsecond=0)
    return this_hour - timedelta(days=100)


def test_build_windows_covers_the_range_in_contiguous_30_day_chunks():
    windows = build_windows(WINDOW_START, WINDOW_END)

    assert len(windows) == 13
    assert windows[0][0] == WINDOW_START
    assert windows[-1][1] == WINDOW_END
    assert windows[-1][1] - windows[-1][0] == timedelta(days=5)
    for start, end in windows:
        assert end - start <= timedelta(days=30)
    for window, next_window in pairwise(windows):
        window_end = window[1]
        next_window_start = next_window[0]
        assert window_end == next_window_start


def test_parse_maps_a_source_row_to_the_database_tuple(payload):
    source_rows = payload["data"]

    parsed_rows = parse(source_rows, RETRIEVED_AT)

    assert len(parsed_rows) == len(source_rows)
    assert parsed_rows[0] == (
        datetime(2026, 7, 31, 23, 30, tzinfo=timezone.utc),
        datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc),
        RETRIEVED_AT,
        166,
        156,
        "moderate",
    )


@pytest.mark.parametrize(
    "response",
    [
        pytest.param([], id="list"),
        pytest.param({}, id="missing-data"),
        pytest.param({"data": []}, id="empty-data"),
    ],
)
def test_process_window_rejects_an_invalid_response_envelope(response):
    conn = Mock()

    with (
        patch(
            "ingestion.carbon_intensity.outturn_poller.fetch",
            return_value=response,
        ),
        patch(
            "ingestion.carbon_intensity.outturn_poller.process_rows"
        ) as mock_process_rows,
    ):
        with pytest.raises(RuntimeError, match="expected a non-empty data list"):
            process_window(conn, WINDOW_START, WINDOW_END, RETRIEVED_AT)

    mock_process_rows.assert_not_called()


@pytest.mark.parametrize(
    "request_end,expected_result",
    [
        pytest.param(
            datetime(2026, 8, 1, 3, 0, tzinfo=timezone.utc),
            (0, False),
            id="complete-window",
        ),
        pytest.param(
            datetime(2026, 8, 1, 3, 30, tzinfo=timezone.utc),
            (0, True),
            id="incomplete-window",
        ),
    ],
)
def test_process_window_stores_rows_before_checking_completeness(
    payload, request_end, expected_result
):
    request_start = datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc)

    with (
        patch(
            "ingestion.carbon_intensity.outturn_poller.fetch",
            return_value=payload,
        ),
        patch(
            "ingestion.carbon_intensity.outturn_poller.insert_rows"
        ) as mock_insert_rows,
    ):
        result = process_window(MagicMock(), request_start, request_end, RETRIEVED_AT)

    assert result == expected_result
    inserted_rows = mock_insert_rows.call_args.args[2]
    assert len(inserted_rows) == len(payload["data"])


def test_process_window_skips_completeness_after_rejected_rows(payload):
    conn = Mock()

    with (
        patch(
            "ingestion.carbon_intensity.outturn_poller.fetch",
            return_value=payload,
        ),
        patch(
            "ingestion.carbon_intensity.outturn_poller.process_rows",
            return_value=2,
        ),
        patch(
            "ingestion.carbon_intensity.outturn_poller.validate_outturn_window"
        ) as mock_validate_completeness,
    ):
        result = process_window(conn, WINDOW_START, WINDOW_END, RETRIEVED_AT)

    assert result == (2, False)
    mock_validate_completeness.assert_not_called()


@pytest.mark.parametrize(
    "summary_output",
    [
        pytest.param((0, None, None), id="empty-table"),
        pytest.param(
            (
                100,
                datetime(2026, 8, 1, tzinfo=timezone.utc),
                datetime(2026, 8, 19, tzinfo=timezone.utc),
            ),
            id="short-history",
        ),
    ],
)
def test_run_backfills_all_windows_and_reports_the_total_failures(
    payload, summary_output
):
    valid_row = payload["data"][0]
    invalid_row = {**valid_row, "intensity": {**valid_row["intensity"], "actual": None}}
    one_row_rejected = {"data": [invalid_row]}
    two_rows_rejected = {"data": [invalid_row, invalid_row]}
    incomplete_window = {"data": [valid_row]}
    conn = MagicMock()
    cursor_of(conn).fetchone.return_value = summary_output

    with (
        clock_reading(RETRIEVED_AT),
        patch(
            "ingestion.carbon_intensity.outturn_poller.fetch",
            side_effect=[one_row_rejected, two_rows_rejected]
            + [incomplete_window] * 11,
        ) as mock_fetch,
    ):
        with pytest.raises(
            ContractRejected,
            match="Quarantined 3 rows.*found 11 incomplete",
        ):
            run_poller(conn)

    requested_windows = []
    for fetch_call in mock_fetch.call_args_list:
        requested_windows.append(fetch_call.args)
    first_start, _ = requested_windows[0]
    _, last_end = requested_windows[-1]
    assert len(requested_windows) == 13
    assert first_start == datetime(2025, 8, 18, 21, 43, tzinfo=timezone.utc)
    assert last_end == datetime(2026, 8, 18, 21, 43, tzinfo=timezone.utc)
    for window, next_window in pairwise(requested_windows):
        _, window_end = window
        next_window_start, _ = next_window
        assert window_end == next_window_start


def test_run_refetches_a_history_gap_after_the_recent_refresh():
    conn = Mock()
    missing_start = a_period_start_100_days_before(RETRIEVED_AT)
    a_later_reading = RETRIEVED_AT + timedelta(minutes=5)

    with (
        clock_reading_in_turn(RETRIEVED_AT, a_later_reading),
        patch(
            "ingestion.carbon_intensity.outturn_poller.stored_period_summary",
            return_value=HISTORY_OLDER_THAN_THE_HORIZON,
        ),
        patch(
            "ingestion.carbon_intensity.outturn_poller.is_history_refetch_day",
            return_value=True,
        ),
        patch(
            "ingestion.carbon_intensity.outturn_poller.missing_period_starts",
            return_value=[missing_start],
        ) as mock_missing_period_starts,
        patch(
            "ingestion.carbon_intensity.outturn_poller.process_window",
            return_value=(0, False),
        ) as mock_process_window,
    ):
        run_poller(conn)

    recent_call, history_call = mock_process_window.call_args_list
    _conn, recent_start, recent_end, retrieved_at = recent_call.args
    _conn, history_start, history_end, history_retrieved_at = history_call.args
    # One capture time for the whole run: every window is stamped with it.
    assert retrieved_at == RETRIEVED_AT
    assert history_retrieved_at == RETRIEVED_AT
    assert recent_end == retrieved_at - timedelta(days=1)
    assert recent_start == recent_end - timedelta(days=7)
    assert history_start <= missing_start < history_end
    assert history_end - history_start <= timedelta(days=30)

    _conn, first_checked, last_checked = mock_missing_period_starts.call_args.args
    horizon_start = recent_end - timedelta(days=365)
    assert horizon_start < first_checked
    assert last_checked < recent_start


def test_run_attempts_every_history_gap_when_one_request_fails():
    conn = Mock()
    older_gap = a_period_start_100_days_before(RETRIEVED_AT) - timedelta(days=100)
    newer_gap = a_period_start_100_days_before(RETRIEVED_AT)
    recent_refresh = (0, False)
    failed_request = RuntimeError("expected a non-empty data list")
    later_window = (0, False)

    with (
        clock_reading(RETRIEVED_AT),
        patch(
            "ingestion.carbon_intensity.outturn_poller.stored_period_summary",
            return_value=HISTORY_OLDER_THAN_THE_HORIZON,
        ),
        patch(
            "ingestion.carbon_intensity.outturn_poller.is_history_refetch_day",
            return_value=True,
        ),
        patch(
            "ingestion.carbon_intensity.outturn_poller.missing_period_starts",
            return_value=[older_gap, newer_gap],
        ),
        patch(
            "ingestion.carbon_intensity.outturn_poller.process_window",
            side_effect=[recent_refresh, failed_request, later_window],
        ) as mock_process_window,
    ):
        run_poller(conn)

    assert mock_process_window.call_count == 3
    conn.rollback.assert_called_once_with()


def run_with_one_history_gap(recent_outcome, history_outcome):
    with (
        clock_reading(RETRIEVED_AT),
        patch(
            "ingestion.carbon_intensity.outturn_poller.stored_period_summary",
            return_value=HISTORY_OLDER_THAN_THE_HORIZON,
        ),
        patch(
            "ingestion.carbon_intensity.outturn_poller.is_history_refetch_day",
            return_value=True,
        ),
        patch(
            "ingestion.carbon_intensity.outturn_poller.missing_period_starts",
            return_value=[a_period_start_100_days_before(RETRIEVED_AT)],
        ),
        patch(
            "ingestion.carbon_intensity.outturn_poller.process_window",
            side_effect=[recent_outcome, history_outcome],
        ),
    ):
        run_poller(Mock())


def test_a_history_gap_the_source_never_fills_does_not_fail_the_run():
    still_incomplete = (0, True)
    run_with_one_history_gap((0, False), still_incomplete)


def test_an_incomplete_recent_window_still_fails_the_run():
    with pytest.raises(RuntimeError, match="Found 1 incomplete"):
        run_with_one_history_gap((0, True), (0, False))


def test_a_database_fault_in_a_history_window_fails_the_run():
    with pytest.raises(psycopg.OperationalError):
        run_with_one_history_gap((0, False), psycopg.OperationalError("lost"))


def test_rows_rejected_in_a_history_window_still_fail_the_run():
    with pytest.raises(ContractRejected, match="Quarantined 2 rows"):
        run_with_one_history_gap((0, False), (2, False))


def test_run_leaves_history_gaps_alone_between_refetch_days():
    conn = Mock()

    with (
        clock_reading(RETRIEVED_AT),
        patch(
            "ingestion.carbon_intensity.outturn_poller.stored_period_summary",
            return_value=HISTORY_OLDER_THAN_THE_HORIZON,
        ),
        patch(
            "ingestion.carbon_intensity.outturn_poller.is_history_refetch_day",
            return_value=False,
        ),
        patch(
            "ingestion.carbon_intensity.outturn_poller.missing_period_starts",
            return_value=[a_period_start_100_days_before(RETRIEVED_AT)],
        ) as mock_missing_period_starts,
        patch(
            "ingestion.carbon_intensity.outturn_poller.process_window",
            return_value=(0, False),
        ) as mock_process_window,
    ):
        run_poller(conn)

    mock_missing_period_starts.assert_not_called()
    assert mock_process_window.call_count == 1


def test_history_is_refetched_on_one_day_a_week():
    first_day = datetime(2026, 10, 5, 6, tzinfo=timezone.utc)
    refetch_days = []
    for day in range(7):
        run_date = first_day + timedelta(days=day)
        if is_history_refetch_day(run_date):
            refetch_days.append(run_date)

    assert len(refetch_days) == 1


@pytest.mark.parametrize(
    "minute,period_minute",
    [
        pytest.param(29, 0, id="last-minute-of-the-first-half-hour"),
        pytest.param(30, 30, id="first-minute-of-the-second-half-hour"),
    ],
)
def test_floor_to_period_returns_the_start_of_the_half_hour(minute, period_minute):
    inside_the_period = datetime(2026, 8, 20, 6, minute, 45, tzinfo=timezone.utc)

    assert floor_to_period(inside_the_period) == datetime(
        2026, 8, 20, 6, period_minute, tzinfo=timezone.utc
    )
