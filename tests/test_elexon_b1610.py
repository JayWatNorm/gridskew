import json
import pathlib
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import ANY, MagicMock, Mock, patch

import pytest

from ingestion.elexon.b1610_poller import (
    decimal_json_dumps,
    load,
    parse,
    quarantine_rows,
)
from ingestion.elexon.b1610_poller import run as run_poller

FIXTURE_PATH = (
    pathlib.Path(__file__).parent / "fixtures" / "elexon" / "b1610_stream.json"
)

# Deliberately not on a period boundary and distinct from every fixture value,
# so a positional swap cannot pass unnoticed.
RETRIEVED_AT = datetime(2026, 8, 17, 7, 47, 13, tzinfo=timezone.utc)


def test_parse():
    results = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"), parse_float=Decimal)

    parsed_results = parse(results, RETRIEVED_AT)

    # Golden value. The expected side is written by hand from the fixture, not
    # derived from it, so it independently verifies both the string-to-datetime
    # conversion and the position of every column in the tuple.
    assert parsed_results[10] == (
        "E__MDRX001",
        "DRAXD-1",
        "Generation",
        date(2026, 8, 9),
        2,
        datetime(2026, 8, 9, 0, 0, tzinfo=timezone.utc),
        "II",
        Decimal("-15.069"),
        RETRIEVED_AT,
    )

    # Fixture breadth and target-key uniqueness.
    assert len(parsed_results) == len(results)
    assert len(results) > 0
    key = [(row[0], row[3], row[4], row[6]) for row in parsed_results]
    assert len(set(key)) == len(key), "duplicate key in batch"
    assert len({row[0] for row in parsed_results}) > 1

    # Covers nullable identifiers, quantity signs, and settlement boundaries.
    assert any(row[1] is None for row in parsed_results)
    assert any(row[1] is not None for row in parsed_results)
    assert any(row[7] < 0 for row in parsed_results)
    assert any(row[7] == 0 for row in parsed_results)
    assert any(row[4] == 1 for row in parsed_results)
    assert len({row[3] for row in parsed_results}) > 1

    # Rules that must hold for every row.
    for row in parsed_results:
        assert type(row[3]) is date
        assert type(row[5]) is datetime
        assert row[5].utcoffset() == timedelta(0)
        assert row[0] is not None
        assert (row[4] >= 1) & (row[4] <= 50)
        assert row[8] == RETRIEVED_AT

        # Period 1 crosses the UTC date boundary of the local settlement day.
        if row[4] == 2:
            assert row[3] == row[5].date()
        if row[4] == 1:
            assert row[3] == row[5].date() + timedelta(days=1)

        # Preserve the source's three-decimal precision.
        assert not isinstance(row[7], float)
        assert -row[7].as_tuple().exponent == 3


def test_execute_values_reqs():
    parsed_rows = [Mock(name="parsed_row")]
    conn = MagicMock()

    with patch("ingestion.elexon.b1610_poller.execute_values") as mock_execute_values:
        load(parsed_rows, conn)
        load(parsed_rows, conn)

    assert mock_execute_values.call_count == 2
    for call in mock_execute_values.call_args_list:
        compact_sql = "".join(call.args[1].split())
        assert (
            "ONCONFLICT(bm_unit,settlement_date,settlement_period,settlement_run_type)"
            "DONOTHING" in compact_sql
        )
        assert call.args[2] is parsed_rows
        assert call.kwargs["page_size"] == 1000
    assert conn.commit.call_count == 2


def test_decimal_json_dumps_preserves_numeric_value():
    dec = Decimal("123.456")
    json_str = decimal_json_dumps(dec)
    parsed_dec = json.loads(json_str, parse_float=Decimal)
    assert isinstance(parsed_dec, Decimal)
    assert parsed_dec.as_tuple() == dec.as_tuple()


def test_empty_fetched_rows():
    fetched_rows = []
    from_date = datetime(2026, 8, 20, tzinfo=timezone.utc)
    to_date = datetime(2026, 8, 21, tzinfo=timezone.utc)
    conn = Mock()

    with (
        patch(
            "ingestion.elexon.b1610_poller.fetch", return_value=fetched_rows
        ) as mock_fetch,
        patch("ingestion.elexon.b1610_poller.parse") as mock_parse,
        patch("ingestion.elexon.b1610_poller.quarantine_rows") as mock_quarantine,
        patch("ingestion.elexon.b1610_poller.load") as mock_load,
    ):
        with pytest.raises(RuntimeError):
            run_poller(conn, from_date, to_date)
        mock_fetch.assert_called_once()
        mock_parse.assert_not_called()
        mock_load.assert_not_called()
        mock_quarantine.assert_not_called()


def test_malformed_response_container():
    fetched_rows = {"data": []}
    from_date = datetime(2026, 8, 20, tzinfo=timezone.utc)
    to_date = datetime(2026, 8, 21, tzinfo=timezone.utc)
    conn = Mock()

    with (
        patch(
            "ingestion.elexon.b1610_poller.fetch", return_value=fetched_rows
        ) as mock_fetch,
        patch("ingestion.elexon.b1610_poller.parse") as mock_parse,
        patch("ingestion.elexon.b1610_poller.quarantine_rows") as mock_quarantine,
        patch("ingestion.elexon.b1610_poller.load") as mock_load,
    ):
        with pytest.raises(RuntimeError):
            run_poller(conn, from_date, to_date)
        mock_fetch.assert_called_once()
        mock_parse.assert_not_called()
        mock_load.assert_not_called()
        mock_quarantine.assert_not_called()


def test_run_routing():
    with open(FIXTURE_PATH, "r", encoding="utf-8") as f:
        results = json.load(f, parse_float=Decimal)

    valid_row = results[0].copy()

    warning_row = results[1].copy()
    warning_row["newPublisherField"] = "observed"

    rejected_row = results[2].copy()
    del rejected_row["halfHourEndTime"]

    fetched_rows = [valid_row, warning_row, rejected_row]
    parsed_rows = object()
    conn = Mock()

    from_date = datetime(2026, 8, 20, tzinfo=timezone.utc)
    to_date = datetime(2026, 8, 21, tzinfo=timezone.utc)

    expected_rejected_finding = {
        "index": 2,
        "row": rejected_row,
        "errors": ["Missing required field: halfHourEndTime"],
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
        patch(
            "ingestion.elexon.b1610_poller.fetch",
            return_value=fetched_rows,
        ),
        patch(
            "ingestion.elexon.b1610_poller.parse", side_effect=record_parse
        ) as mock_parse,
        patch(
            "ingestion.elexon.b1610_poller.quarantine_rows",
            side_effect=record_quarantine,
        ) as mock_quarantine,
        patch(
            "ingestion.elexon.b1610_poller.load", side_effect=record_load
        ) as mock_load,
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
        results = json.load(f, parse_float=Decimal)
    receive_date = datetime(2026, 8, 21, 10, 30, 0, tzinfo=timezone.utc)
    rejected_row = results[10].copy()
    del rejected_row["halfHourEndTime"]
    from_date = datetime(2026, 8, 20, tzinfo=timezone.utc)
    to_date = datetime(2026, 8, 21, tzinfo=timezone.utc)
    request_context = {
        "from": from_date.isoformat(),
        "to": to_date.isoformat(),
    }

    expected_rejected_finding = {
        "index": 2,
        "row": rejected_row,
        "errors": ["Missing required field: halfHourEndTime"],
        "warnings": [],
    }

    conn = MagicMock()

    with patch("ingestion.elexon.b1610_poller.execute_values") as mock_execute_values:
        quarantine_rows(
            [expected_rejected_finding], conn, receive_date, request_context
        )

        mock_execute_values.assert_called_once()
        actual_args, _ = mock_execute_values.call_args
        insert_values = actual_args[2]
        payload_adapter = insert_values[0][5]
        json_text = payload_adapter.dumps(payload_adapter.adapted)
        decoded_payload = json.loads(json_text, parse_float=Decimal)

        assert isinstance(decoded_payload["quantity"], Decimal)
        assert (
            decoded_payload["quantity"].as_tuple()
            == rejected_row["quantity"].as_tuple()
        )
        assert insert_values[0][3].adapted == {
            "source_index": 2,
            "errors": ["Missing required field: halfHourEndTime"],
        }
        conn.commit.assert_called_once_with()
        assert insert_values[0][5].adapted == expected_rejected_finding["row"]


def test_run_error_only_routing():
    with open(FIXTURE_PATH, "r", encoding="utf-8") as f:
        results = json.load(f)
    rejected_row = results[0].copy()
    rejected_row["quantity"] = "Thirty"  # Replace quantity with a string.
    from_date = datetime(2026, 8, 20, tzinfo=timezone.utc)
    to_date = datetime(2026, 8, 21, tzinfo=timezone.utc)
    request_context = {
        "from": from_date.isoformat(),
        "to": to_date.isoformat(),
    }
    expected_rejected_finding = {
        "index": 0,
        "row": rejected_row,
        "errors": ["Invalid data type detected: quantity"],
        "warnings": [],
    }
    conn = Mock()

    with (
        patch("ingestion.elexon.b1610_poller.fetch", return_value=[rejected_row]),
        patch("ingestion.elexon.b1610_poller.parse") as mock_parse,
        patch("ingestion.elexon.b1610_poller.quarantine_rows") as mock_quarantine,
        patch("ingestion.elexon.b1610_poller.load") as mock_load,
    ):
        with pytest.raises(RuntimeError):
            run_poller(conn, from_date, to_date)
        mock_parse.assert_not_called()
        mock_load.assert_not_called()
        mock_quarantine.assert_called_once_with(
            [expected_rejected_finding], conn, ANY, request_context
        )


def test_run_logs_grouped_warning_rows(caplog):
    with open(FIXTURE_PATH, "r", encoding="utf-8") as f:
        results = json.load(f, parse_float=Decimal)

    warning_row = results[1].copy()
    warning_row["newPublisherField"] = "observed"
    valid_row = results[10].copy()
    warning_row2 = results[1].copy()
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
        patch("ingestion.elexon.b1610_poller.fetch", return_value=fetched_rows),
        patch(
            "ingestion.elexon.b1610_poller.parse",
            return_value=parsed_rows,
        ) as mock_parse,
        patch("ingestion.elexon.b1610_poller.quarantine_rows") as mock_quarantine,
        patch("ingestion.elexon.b1610_poller.load") as mock_load,
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
            assert payload["dataset"] == "B1610"
            assert payload["severity"] == "warning"
            assert payload["request_context"] == {
                "from": from_date.isoformat(),
                "to": to_date.isoformat(),
            }
            assert payload["retrieved_at"] == actual_retrieved_at.isoformat()


def test_run_caps_grouped_warning_source_indexes(caplog):
    with open(FIXTURE_PATH, "r", encoding="utf-8") as f:
        results = json.load(f, parse_float=Decimal)

    fetched_rows = []
    for index in range(6):
        warning_row = results[index].copy()
        warning_row["newPublisherField"] = "observed"
        fetched_rows.append(warning_row)

    conn = Mock()
    from_date = datetime(2026, 8, 20, tzinfo=timezone.utc)
    to_date = datetime(2026, 8, 21, tzinfo=timezone.utc)

    with (
        patch("ingestion.elexon.b1610_poller.fetch", return_value=fetched_rows),
        patch("ingestion.elexon.b1610_poller.parse", return_value=object()),
        patch("ingestion.elexon.b1610_poller.quarantine_rows") as mock_quarantine,
        patch("ingestion.elexon.b1610_poller.load"),
    ):
        run_poller(conn, from_date, to_date)

    assert len(caplog.records) == 1
    payload = json.loads(caplog.records[0].getMessage())
    assert payload["affected_row_count"] == 6
    assert payload["sample_source_indexes"] == [0, 1, 2, 3, 4]
    mock_quarantine.assert_not_called()


def test_invalid_dictionary():
    with open(FIXTURE_PATH, "r", encoding="utf-8") as f:
        results = json.load(f, parse_float=Decimal)

    valid_row = results[1].copy()
    mixed_rows = [None, valid_row]

    expected_finding = {
        "index": 0,
        "row": None,
        "errors": ["Invalid row type: expected dictionary"],
        "warnings": [],
    }

    conn = Mock()
    from_date = datetime(2026, 8, 20, tzinfo=timezone.utc)
    to_date = datetime(2026, 8, 21, tzinfo=timezone.utc)

    request_context = {
        "from": from_date.isoformat(),
        "to": to_date.isoformat(),
    }

    parsed_rows = object()

    with (
        patch("ingestion.elexon.b1610_poller.fetch", return_value=mixed_rows),
        patch(
            "ingestion.elexon.b1610_poller.parse", return_value=parsed_rows
        ) as mock_parse,
        patch("ingestion.elexon.b1610_poller.quarantine_rows") as mock_quarantine,
        patch("ingestion.elexon.b1610_poller.load") as mock_load,
    ):
        with pytest.raises(RuntimeError):
            run_poller(conn, from_date, to_date)

        mock_quarantine.assert_called_with(
            [expected_finding], conn, ANY, request_context
        )
        mock_parse.assert_called_with([mixed_rows[1]], ANY)
        mock_load.assert_called_with(parsed_rows, conn)
