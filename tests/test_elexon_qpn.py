import json
import pathlib
from datetime import date, datetime, timedelta, timezone
from unittest.mock import ANY, MagicMock, Mock, patch

import pytest

from ingestion.elexon.qpn_poller import load, parse, quarantine_rows
from ingestion.elexon.qpn_poller import run as run_poller

FIXTURE_PATH = pathlib.Path(__file__).parent / "fixtures" / "elexon" / "qpn_stream.json"

# Deliberately not on a period boundary, and distinct from every other value in
# the golden tuple, so that a positional swap cannot pass unnoticed.
RETRIEVED_AT = datetime(2026, 8, 17, 7, 47, 13, tzinfo=timezone.utc)


def test_parse():
    with open(FIXTURE_PATH, "r", encoding="utf-8") as f:
        results = json.load(f)

    parsed_results = parse(results, RETRIEVED_AT)

    # Parse preserves row count.
    assert len(parsed_results) == len(results)
    assert len(results) > 0

    # Golden value. The expected side is written by hand from the fixture, not
    # derived from it, so it independently verifies both the string-to-datetime
    # conversion and the position of every column in the tuple.
    assert parsed_results[16] == (
        date(2026, 8, 21),
        36,
        datetime(2026, 8, 21, 16, 30, tzinfo=timezone.utc),
        datetime(2026, 8, 21, 17, 00, tzinfo=timezone.utc),
        -60,
        -60,
        "WILCT-1",
        "T_WILCT-1",
        RETRIEVED_AT,
    )

    # Rules that must hold for every row.
    for i, row in enumerate(parsed_results, start=1):
        assert row[2] < row[3]
        assert row[8] == RETRIEVED_AT, (
            f"row {i}: retrieved_at is {row[2]}, expected {RETRIEVED_AT}"
        )
        assert (row[1] >= 1) & (row[1] <= 50)
        assert type(row[0]) is date
        assert type(row[2]) is datetime
        assert type(row[3]) is datetime
        assert row[2].utcoffset() == timedelta(0)
        assert row[3].utcoffset() == timedelta(0)

    # Fixture breadth and target-key uniqueness.
    key = [(row[6], row[2]) for row in parsed_results]
    assert len(set(key)) == len(key), "duplicate key in batch"
    assert any(row[4] < 0 for row in parsed_results)
    assert all(row[3] - row[2] == timedelta(minutes=30) for row in parsed_results)
    assert len({row[6] for row in parsed_results}) > 1


def test_execute_values_reqs():
    parsed_rows = [Mock(name="parsed_row")]
    conn = MagicMock()

    with patch("ingestion.elexon.qpn_poller.execute_values") as mock_execute_values:
        load(parsed_rows, conn)
        load(parsed_rows, conn)

    assert mock_execute_values.call_count == 2
    for call in mock_execute_values.call_args_list:
        compact_sql = "".join(call.args[1].split())
        assert (
            "ONCONFLICT(national_grid_bm_unit,time_from,retrieved_at)DONOTHING"
            in compact_sql
        )
        assert call.args[2] is parsed_rows
        assert call.kwargs["page_size"] == 1000
    assert conn.commit.call_count == 2


def test_run_routing():
    with open(FIXTURE_PATH, "r", encoding="utf-8") as f:
        results = json.load(f)

    valid_row = results[0].copy()
    warning_row = results[1].copy()
    warning_row["newPublisherField"] = "observed"
    rejected_row = results[2].copy()
    del rejected_row["settlementPeriod"]

    fetched_rows = [valid_row, warning_row, rejected_row]
    parsed_rows = object()
    conn = Mock()
    from_date = datetime(2026, 8, 20, tzinfo=timezone.utc)
    to_date = datetime(2026, 8, 21, tzinfo=timezone.utc)

    expected_rejected_finding = {
        "index": 2,
        "row": rejected_row,
        "errors": ["Missing required field: settlementPeriod"],
        "warnings": [],
    }
    event_record = []

    def record_quarantine(*args, **kwargs):
        event_record.append("quarantine")

    def record_parse(*args, **kwargs):
        event_record.append("parse")
        return parsed_rows

    def record_load(*args, **kwargs):
        event_record.append("load")

    with (
        patch("ingestion.elexon.qpn_poller.fetch", return_value=fetched_rows),
        patch(
            "ingestion.elexon.qpn_poller.parse", side_effect=record_parse
        ) as mock_parse,
        patch(
            "ingestion.elexon.qpn_poller.quarantine_rows",
            side_effect=record_quarantine,
        ) as mock_quarantine,
        patch("ingestion.elexon.qpn_poller.load", side_effect=record_load) as mock_load,
    ):
        with pytest.raises(RuntimeError):
            run_poller(conn, from_date, to_date)

    assert event_record == ["quarantine", "parse", "load"]
    mock_parse.assert_called_once_with([valid_row, warning_row], ANY)
    mock_load.assert_called_once_with(parsed_rows, conn)
    mock_quarantine.assert_called_once_with(
        [expected_rejected_finding],
        conn,
        ANY,
        {
            "from": from_date.isoformat(),
            "to": to_date.isoformat(),
        },
    )


def test_quarantine_rows():
    with open(FIXTURE_PATH, "r", encoding="utf-8") as f:
        results = json.load(f)

    retrieved_at = datetime(2026, 8, 21, 10, 30, tzinfo=timezone.utc)
    rejected_row = results[0].copy()
    del rejected_row["settlementPeriod"]
    request_context = {
        "from": datetime(2026, 8, 20, tzinfo=timezone.utc).isoformat(),
        "to": datetime(2026, 8, 21, tzinfo=timezone.utc).isoformat(),
    }
    expected_rejected_finding = {
        "index": 2,
        "row": rejected_row,
        "errors": ["Missing required field: settlementPeriod"],
        "warnings": [],
    }
    conn = MagicMock()

    with patch("ingestion.elexon.qpn_poller.execute_values") as mock_execute_values:
        quarantine_rows(
            [expected_rejected_finding], conn, retrieved_at, request_context
        )

    mock_execute_values.assert_called_once()
    actual_args, _ = mock_execute_values.call_args
    insert_values = actual_args[2]
    assert insert_values[0][0] == "QPN"
    assert insert_values[0][3].adapted == {
        "source_index": 2,
        "errors": ["Missing required field: settlementPeriod"],
    }
    assert insert_values[0][5].adapted == expected_rejected_finding["row"]
    conn.commit.assert_called_once_with()


def test_run_logs_grouped_warning_rows(caplog):
    with open(FIXTURE_PATH, "r", encoding="utf-8") as f:
        results = json.load(f)

    valid_row = results[0].copy()
    warning_row = results[1].copy()
    warning_row["newPublisherField"] = "observed"
    warning_row2 = results[2].copy()
    del warning_row2["dataset"]
    warning_row3 = results[3].copy()
    warning_row3["newPublisherField"] = "observed"
    warning_row3["newPublisherField2"] = "observed2"
    del warning_row3["dataset"]
    fetched_rows = [valid_row, warning_row, warning_row2, warning_row3]

    # Expected groups by source index: missing dataset [2, 3],
    # newPublisherField [1, 3], and newPublisherField2 [3].

    conn = Mock()
    from_date = datetime(2026, 8, 20, tzinfo=timezone.utc)
    to_date = datetime(2026, 8, 21, tzinfo=timezone.utc)
    parsed_rows = object()

    with (
        patch("ingestion.elexon.qpn_poller.fetch", return_value=fetched_rows),
        patch(
            "ingestion.elexon.qpn_poller.parse", return_value=parsed_rows
        ) as mock_parse,
        patch("ingestion.elexon.qpn_poller.quarantine_rows") as mock_quarantine,
        patch("ingestion.elexon.qpn_poller.load") as mock_load,
    ):
        run_poller(conn, from_date, to_date)

    mock_parse.assert_called_once_with(fetched_rows, ANY)
    mock_load.assert_called_once_with(parsed_rows, conn)
    mock_quarantine.assert_not_called()
    assert len(caplog.records) == 3

    payloads = [json.loads(record.getMessage()) for record in caplog.records]
    payloads_by_group = {
        (payload["reason"], payload["field"]): payload for payload in payloads
    }
    assert (
        payloads_by_group[("Optional field is missing", "dataset")][
            "affected_row_count"
        ]
        == 2
    )
    assert (
        payloads_by_group[("Unexpected field", "newPublisherField")][
            "affected_row_count"
        ]
        == 2
    )
    assert (
        payloads_by_group[("Unexpected field", "newPublisherField2")][
            "affected_row_count"
        ]
        == 1
    )
    assert payloads_by_group[("Optional field is missing", "dataset")][
        "sample_source_indexes"
    ] == [2, 3]
    assert payloads_by_group[("Unexpected field", "newPublisherField")][
        "sample_source_indexes"
    ] == [1, 3]
    assert payloads_by_group[("Unexpected field", "newPublisherField2")][
        "sample_source_indexes"
    ] == [3]
    assert all(record.levelname == "WARNING" for record in caplog.records)

    actual_retrieved_at = mock_parse.call_args.args[1]
    for payload in payloads:
        assert payload["dataset"] == "QPN"
        assert payload["severity"] == "warning"
        assert payload["request_context"] == {
            "from": from_date.isoformat(),
            "to": to_date.isoformat(),
        }
        assert payload["retrieved_at"] == actual_retrieved_at.isoformat()


def test_run_caps_grouped_warning_source_indexes(caplog):
    with open(FIXTURE_PATH, "r", encoding="utf-8") as f:
        results = json.load(f)

    fetched_rows = []
    for index in range(6):
        warning_row = results[index].copy()
        warning_row["newPublisherField"] = "observed"
        fetched_rows.append(warning_row)

    conn = Mock()
    from_date = datetime(2026, 8, 20, tzinfo=timezone.utc)
    to_date = datetime(2026, 8, 21, tzinfo=timezone.utc)

    with (
        patch("ingestion.elexon.qpn_poller.fetch", return_value=fetched_rows),
        patch("ingestion.elexon.qpn_poller.parse", return_value=object()),
        patch("ingestion.elexon.qpn_poller.quarantine_rows") as mock_quarantine,
        patch("ingestion.elexon.qpn_poller.load"),
    ):
        run_poller(conn, from_date, to_date)

    assert len(caplog.records) == 1
    payload = json.loads(caplog.records[0].getMessage())
    assert payload["affected_row_count"] == 6
    assert payload["sample_source_indexes"] == [0, 1, 2, 3, 4]
    mock_quarantine.assert_not_called()


def test_run_error_only_routing():
    with open(FIXTURE_PATH, "r", encoding="utf-8") as f:
        results = json.load(f)

    rejected_row = results[0].copy()
    del rejected_row["settlementPeriod"]
    from_date = datetime(2026, 8, 20, tzinfo=timezone.utc)
    to_date = datetime(2026, 8, 21, tzinfo=timezone.utc)
    request_context = {
        "from": from_date.isoformat(),
        "to": to_date.isoformat(),
    }
    conn = Mock()

    with (
        patch("ingestion.elexon.qpn_poller.fetch", return_value=[rejected_row]),
        patch("ingestion.elexon.qpn_poller.parse") as mock_parse,
        patch("ingestion.elexon.qpn_poller.quarantine_rows") as mock_quarantine,
        patch("ingestion.elexon.qpn_poller.load") as mock_load,
    ):
        with pytest.raises(RuntimeError):
            run_poller(conn, from_date, to_date)

    mock_parse.assert_not_called()
    mock_load.assert_not_called()
    mock_quarantine.assert_called_once_with(ANY, conn, ANY, request_context)


def test_empty_fetched_rows():
    fetched_rows = []
    conn = Mock()
    from_date = datetime(2026, 8, 20, tzinfo=timezone.utc)
    to_date = datetime(2026, 8, 21, tzinfo=timezone.utc)

    with (
        patch(
            "ingestion.elexon.qpn_poller.fetch", return_value=fetched_rows
        ) as mock_fetch,
        patch("ingestion.elexon.qpn_poller.parse") as mock_parse,
        patch("ingestion.elexon.qpn_poller.quarantine_rows") as mock_quarantine,
        patch("ingestion.elexon.qpn_poller.load") as mock_load,
    ):
        with pytest.raises(RuntimeError):
            run_poller(conn, from_date, to_date)

    mock_fetch.assert_called_once()
    mock_parse.assert_not_called()
    mock_load.assert_not_called()
    mock_quarantine.assert_not_called()


def test_malformed_response_container():
    fetched_rows = {"data": []}
    conn = Mock()
    from_date = datetime(2026, 8, 20, tzinfo=timezone.utc)
    to_date = datetime(2026, 8, 21, tzinfo=timezone.utc)

    with (
        patch(
            "ingestion.elexon.qpn_poller.fetch", return_value=fetched_rows
        ) as mock_fetch,
        patch("ingestion.elexon.qpn_poller.parse") as mock_parse,
        patch("ingestion.elexon.qpn_poller.quarantine_rows") as mock_quarantine,
        patch("ingestion.elexon.qpn_poller.load") as mock_load,
    ):
        with pytest.raises(RuntimeError):
            run_poller(conn, from_date, to_date)

    mock_fetch.assert_called_once()
    mock_parse.assert_not_called()
    mock_load.assert_not_called()
    mock_quarantine.assert_not_called()
