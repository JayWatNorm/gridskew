import json
from datetime import datetime, timedelta, timezone
from itertools import pairwise
from pathlib import Path
from unittest.mock import MagicMock, Mock, call, patch

import pytest
from fakes import single_spaced

from ingestion.carbon_intensity.contracts import OUTTURN_SPEC
from ingestion.carbon_intensity.outturn_poller import (
    build_windows,
    floor_to_period,
    is_history_refetch_day,
    load,
    parse,
    process_window,
)
from ingestion.carbon_intensity.outturn_poller import run as run_poller

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


def a_period_start_100_days_ago():
    """`run` reads the real clock, so a missing period is placed relative to now."""

    this_hour = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
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


def test_load_uses_the_expected_columns_conflict_key_and_commits():
    parsed_rows = [Mock(name="parsed_row")]
    conn = MagicMock()

    with patch(
        "ingestion.carbon_intensity.outturn_poller.execute_values"
    ) as mock_execute_values:
        load(parsed_rows, conn)

    _cursor, insert_sql, inserted_rows = mock_execute_values.call_args.args
    assert single_spaced(insert_sql) == (
        "INSERT INTO raw.carbon_intensity_outturn (period_start, period_end, "
        "retrieved_at, actual, forecast_final, intensity_index) "
        "VALUES %s "
        "ON CONFLICT (period_start, retrieved_at) DO NOTHING"
    )
    assert inserted_rows == parsed_rows
    conn.commit.assert_called_once_with()


@pytest.mark.parametrize(
    "response",
    [
        pytest.param(None, id="none"),
        pytest.param([], id="list"),
        pytest.param({}, id="missing-data"),
        pytest.param({"data": {}}, id="data-not-a-list"),
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


def test_process_window_stores_rows_before_checking_completeness(payload):
    conn = Mock()
    request_start = datetime(2026, 8, 20, 12, 34, tzinfo=timezone.utc)
    request_end = datetime(2026, 8, 21, 12, 34, tzinfo=timezone.utc)
    events = []

    def record_storage(*args, **kwargs):
        events.append("stored")
        return 0

    def record_completeness_check(*args, **kwargs):
        events.append("checked")

    with (
        patch(
            "ingestion.carbon_intensity.outturn_poller.fetch",
            return_value=payload,
        ),
        patch(
            "ingestion.carbon_intensity.outturn_poller.process_rows",
            side_effect=record_storage,
        ) as mock_process_rows,
        patch(
            "ingestion.carbon_intensity.outturn_poller.validate_outturn_window",
            side_effect=record_completeness_check,
        ) as mock_validate_completeness,
    ):
        result = process_window(conn, request_start, request_end, RETRIEVED_AT)

    mock_process_rows.assert_called_once_with(
        payload["data"],
        spec=OUTTURN_SPEC,
        dataset="CI_Outturn",
        conn=conn,
        retrieved_at=RETRIEVED_AT,
        request_context={
            "from": request_start.isoformat(),
            "to": request_end.isoformat(),
        },
        parse_rows=parse,
        load_rows=load,
    )
    mock_validate_completeness.assert_called_once_with(
        payload["data"],
        request_start,
        request_end,
    )
    assert result == (0, False)
    assert events == ["stored", "checked"]


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
def test_run_backfills_all_windows_and_reports_the_total_failures(summary_output):
    conn = Mock()
    windows = [
        (WINDOW_START, WINDOW_START + timedelta(days=30)),
        (WINDOW_START + timedelta(days=30), WINDOW_START + timedelta(days=60)),
        (WINDOW_START + timedelta(days=60), WINDOW_END),
    ]

    with (
        patch(
            "ingestion.carbon_intensity.outturn_poller.stored_period_summary",
            return_value=summary_output,
        ),
        patch(
            "ingestion.carbon_intensity.outturn_poller.build_windows",
            return_value=windows,
        ),
        patch(
            "ingestion.carbon_intensity.outturn_poller.process_window",
            side_effect=[(1, False), (0, True), (2, False)],
        ) as mock_process_window,
    ):
        with pytest.raises(
            RuntimeError,
            match="Quarantined 3 rows.*found 1 incomplete",
        ):
            run_poller(conn)

    calls = mock_process_window.call_args_list
    assert len(calls) == 3
    shared_capture_time = calls[0].args[3]
    for window_call, window in zip(calls, windows, strict=True):
        window_start, window_end = window
        assert window_call.args == (conn, window_start, window_end, shared_capture_time)


def test_run_refreshes_only_the_recent_window_when_history_is_complete():
    conn = Mock()

    with (
        patch(
            "ingestion.carbon_intensity.outturn_poller.stored_period_summary",
            return_value=(
                100,
                datetime(2020, 1, 1, tzinfo=timezone.utc),
                datetime(2026, 8, 1, tzinfo=timezone.utc),
            ),
        ),
        patch(
            "ingestion.carbon_intensity.outturn_poller.is_history_refetch_day",
            return_value=True,
        ),
        patch(
            "ingestion.carbon_intensity.outturn_poller.missing_period_starts",
            return_value=[],
        ),
        patch(
            "ingestion.carbon_intensity.outturn_poller.process_window",
            return_value=(0, False),
        ) as mock_process_window,
    ):
        run_poller(conn)

    conn_arg, start, end, retrieved_at = mock_process_window.call_args.args
    assert conn_arg is conn
    assert end == retrieved_at - timedelta(days=1)
    assert start == end - timedelta(days=7)
    assert mock_process_window.call_args_list == [call(conn, start, end, retrieved_at)]


def test_run_refetches_a_history_gap_after_the_recent_refresh():
    conn = Mock()
    missing_start = a_period_start_100_days_ago()

    with (
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
    _conn, history_start, history_end, _retrieved_at = history_call.args
    assert recent_end == retrieved_at - timedelta(days=1)
    assert recent_start == recent_end - timedelta(days=7)
    assert history_start <= missing_start < history_end
    assert history_end - history_start <= timedelta(days=30)

    _conn, first_checked, last_checked = mock_missing_period_starts.call_args.args
    horizon_start = recent_end - timedelta(days=365)
    assert first_checked == floor_to_period(horizon_start) + timedelta(days=1)
    assert last_checked == floor_to_period(recent_start) - timedelta(minutes=30)


def test_run_attempts_every_history_gap_when_one_request_fails():
    conn = Mock()
    older_gap = a_period_start_100_days_ago() - timedelta(days=100)
    newer_gap = a_period_start_100_days_ago()
    recent_refresh = (0, False)
    failed_request = RuntimeError("expected a non-empty data list")
    later_window = (0, False)

    with (
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
        with pytest.raises(RuntimeError, match="Found 1 incomplete"):
            run_poller(conn)

    assert mock_process_window.call_count == 3
    conn.rollback.assert_called_once_with()


def test_run_leaves_history_gaps_alone_between_refetch_days():
    conn = Mock()

    with (
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
            return_value=[a_period_start_100_days_ago()],
        ) as mock_missing_period_starts,
        patch(
            "ingestion.carbon_intensity.outturn_poller.process_window",
            return_value=(0, False),
        ) as mock_process_window,
    ):
        run_poller(conn)

    mock_missing_period_starts.assert_not_called()
    assert mock_process_window.call_count == 1


@pytest.mark.parametrize(
    "run_date,expected",
    [
        pytest.param(datetime(2026, 10, 5, 6, tzinfo=timezone.utc), True, id="monday"),
        pytest.param(
            datetime(2026, 10, 6, 6, tzinfo=timezone.utc), False, id="tuesday"
        ),
    ],
)
def test_history_is_refetched_on_one_day_a_week(run_date, expected):
    assert is_history_refetch_day(run_date) is expected


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
