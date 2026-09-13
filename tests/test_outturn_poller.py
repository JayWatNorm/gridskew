import json
from datetime import datetime, timedelta, timezone
from itertools import pairwise
from pathlib import Path
from unittest.mock import MagicMock, Mock, call, patch

import pytest

from ingestion.carbon_intensity.contracts import OUTTURN_SPEC
from ingestion.carbon_intensity.outturn_poller import (
    build_windows,
    load,
    parse,
    process_window,
)
from ingestion.carbon_intensity.outturn_poller import run as run_poller

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "carbon_intensity" / "outturn.json"
RETRIEVED_AT = datetime(2026, 8, 19, 21, 43, 17, tzinfo=timezone.utc)
WINDOW_START = datetime(2025, 8, 20, tzinfo=timezone.utc)
WINDOW_END = datetime(2026, 8, 20, tzinfo=timezone.utc)


@pytest.fixture
def payload():
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_build_windows_covers_the_range_in_contiguous_30_day_chunks():
    windows = build_windows(WINDOW_START, WINDOW_END)

    assert len(windows) == 13
    assert windows[0][0] == WINDOW_START
    assert windows[-1][1] == WINDOW_END
    assert windows[-1][1] - windows[-1][0] == timedelta(days=5)
    assert all(end - start <= timedelta(days=30) for start, end in windows)
    assert all(current[1] == following[0] for current, following in pairwise(windows))


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

    sql = "".join(mock_execute_values.call_args.args[1].split())
    assert sql == (
        "INSERTINTOraw.carbon_intensity_outturn(period_start,period_end,"
        "retrieved_at,actual,forecast_final,intensity_index)VALUES%s"
        "ONCONFLICT(period_start,retrieved_at)DONOTHING"
    )
    assert mock_execute_values.call_args.args[2] == parsed_rows
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

    assert mock_process_window.call_count == 3
    capture_times = [item.args[3] for item in mock_process_window.call_args_list]
    assert len(set(capture_times)) == 1
    assert [item.args[:3] for item in mock_process_window.call_args_list] == [
        (conn, start, end) for start, end in windows
    ]


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
