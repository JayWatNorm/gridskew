import json
import logging
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, Mock, patch

import pytest

from ingestion.carbon_intensity.contracts import FORECAST_SPEC
from ingestion.elexon.b1610_poller import decimal_json_dumps
from ingestion.routing import process_rows, quarantine_rows

RETRIEVED_AT = datetime(2026, 8, 21, 10, 30, tzinfo=timezone.utc)
REQUEST_CONTEXT = {"timestamp": "2026-08-21T10:30Z", "horizon_hours": 48}


def make_source_row():
    return {
        "from": "2026-08-21T10:30Z",
        "to": "2026-08-21T11:00Z",
        "intensity": {"forecast": 182, "actual": None, "index": "moderate"},
    }


def test_process_rows_routes_mixed_batch():
    valid_row = make_source_row()
    warning_row = deepcopy(valid_row)
    warning_row["intensity"]["publisherNote"] = "observed"
    rejected_row = deepcopy(valid_row)
    del rejected_row["intensity"]["forecast"]
    rows = [valid_row, warning_row, rejected_row]
    parsed_rows = object()
    conn = Mock()
    events = []

    def record_parse(_rows, _retrieved_at):
        events.append("parse")
        return parsed_rows

    def record_load(_rows, _connection):
        events.append("load")

    def record_quarantine(
        _rows,
        _connection,
        _dataset,
        _retrieved_at,
        _request_context,
        payload_dumps=None,
    ):
        events.append("quarantine")

    parse_rows = Mock(side_effect=record_parse)
    load_rows = Mock(side_effect=record_load)

    with patch(
        "ingestion.routing.quarantine_rows",
        side_effect=record_quarantine,
    ) as mock_quarantine:
        rejected_count = process_rows(
            rows,
            spec=FORECAST_SPEC,
            dataset="CI_Forecast",
            conn=conn,
            retrieved_at=RETRIEVED_AT,
            request_context=REQUEST_CONTEXT,
            parse_rows=parse_rows,
            load_rows=load_rows,
        )

    assert rejected_count == 1
    assert events == ["quarantine", "parse", "load"]
    parse_rows.assert_called_once_with([valid_row, warning_row], RETRIEVED_AT)
    load_rows.assert_called_once_with(parsed_rows, conn)
    rejected_finding = mock_quarantine.call_args.args[0][0]
    assert rejected_finding == {
        "index": 2,
        "row": rejected_row,
        "errors": ["Missing required field: intensity.forecast"],
        "warnings": [],
    }


def test_process_rows_skips_typed_load_when_all_rows_are_rejected():
    rejected_row = make_source_row()
    del rejected_row["intensity"]
    conn = Mock()
    parse_rows = Mock()
    load_rows = Mock()

    with patch("ingestion.routing.quarantine_rows") as mock_quarantine:
        rejected_count = process_rows(
            [rejected_row],
            spec=FORECAST_SPEC,
            dataset="CI_Forecast",
            conn=conn,
            retrieved_at=RETRIEVED_AT,
            request_context=REQUEST_CONTEXT,
            parse_rows=parse_rows,
            load_rows=load_rows,
        )

    assert rejected_count == 1
    mock_quarantine.assert_called_once()
    parse_rows.assert_not_called()
    load_rows.assert_not_called()


def test_process_rows_groups_warning_logs_and_caps_source_indexes(caplog):
    rows = []
    for index in range(6):
        row = make_source_row()
        row["publisherNote"] = "observed"
        if index == 5:
            row["intensity"]["publisherStatus"] = "provisional"
        rows.append(row)

    caplog.set_level(logging.WARNING, logger="ingestion.routing")
    process_rows(
        rows,
        spec=FORECAST_SPEC,
        dataset="CI_Forecast",
        conn=Mock(),
        retrieved_at=RETRIEVED_AT,
        request_context=REQUEST_CONTEXT,
        parse_rows=Mock(return_value=object()),
        load_rows=Mock(),
    )

    payloads = [json.loads(record.getMessage()) for record in caplog.records]
    payloads_by_field = {payload["field"]: payload for payload in payloads}

    assert len(payloads) == 2
    assert payloads_by_field["publisherNote"]["affected_row_count"] == 6
    assert payloads_by_field["publisherNote"]["sample_source_indexes"] == [
        0,
        1,
        2,
        3,
        4,
    ]
    assert payloads_by_field["intensity.publisherStatus"]["sample_source_indexes"] == [
        5
    ]
    assert all(payload["dataset"] == "CI_Forecast" for payload in payloads)


@pytest.mark.parametrize(
    ("dataset", "request_context"),
    [
        pytest.param("CI_Forecast", REQUEST_CONTEXT, id="forecast"),
        pytest.param(
            "CI_Outturn",
            {
                "from": "2026-08-20T00:00:00+00:00",
                "to": "2026-08-21T00:00:00+00:00",
            },
            id="outturn",
        ),
    ],
)
def test_quarantine_rows_preserves_evidence(dataset, request_context):
    row = make_source_row()
    finding = {
        "index": 3,
        "row": row,
        "errors": ["Null not permitted: intensity.actual"],
        "warnings": [],
    }
    conn = MagicMock()

    with patch("ingestion.routing.execute_values") as mock_execute_values:
        quarantine_rows([finding], conn, dataset, RETRIEVED_AT, request_context)

    inserted = mock_execute_values.call_args.args[2][0]
    assert inserted[0] == dataset
    assert inserted[1] == RETRIEVED_AT
    assert inserted[2].adapted == request_context
    assert inserted[3].adapted == {
        "source_index": 3,
        "errors": ["Null not permitted: intensity.actual"],
    }
    assert inserted[4].adapted == [
        "from",
        "to",
        "intensity",
        "intensity.forecast",
        "intensity.actual",
        "intensity.index",
    ]
    assert inserted[5].adapted == row
    conn.commit.assert_called_once_with()


def test_quarantine_rows_uses_custom_payload_encoder():
    row = {"quantity": Decimal("123.456")}
    finding = {
        "index": 0,
        "row": row,
        "errors": ["Invalid data type detected: quantity"],
        "warnings": [],
    }
    conn = MagicMock()

    with patch("ingestion.routing.execute_values") as mock_execute_values:
        quarantine_rows(
            [finding],
            conn,
            "B1610",
            RETRIEVED_AT,
            REQUEST_CONTEXT,
            payload_dumps=decimal_json_dumps,
        )

    payload = mock_execute_values.call_args.args[2][0][5]
    decoded = json.loads(payload.dumps(payload.adapted), parse_float=Decimal)
    assert decoded == row


def test_process_rows_stops_when_quarantine_fails():
    rejected_row = make_source_row()
    del rejected_row["intensity"]
    parse_rows = Mock()
    load_rows = Mock()

    with patch(
        "ingestion.routing.quarantine_rows",
        side_effect=OSError("quarantine unavailable"),
    ):
        with pytest.raises(OSError, match="quarantine unavailable"):
            process_rows(
                [rejected_row],
                spec=FORECAST_SPEC,
                dataset="CI_Forecast",
                conn=Mock(),
                retrieved_at=RETRIEVED_AT,
                request_context=REQUEST_CONTEXT,
                parse_rows=parse_rows,
                load_rows=load_rows,
            )

    parse_rows.assert_not_called()
    load_rows.assert_not_called()


def test_process_rows_does_not_change_source_rows():
    valid_row = make_source_row()
    rejected_row = deepcopy(valid_row)
    del rejected_row["intensity"]["forecast"]
    rows = [valid_row, rejected_row]
    original_rows = deepcopy(rows)

    with patch("ingestion.routing.execute_values"):
        process_rows(
            rows,
            spec=FORECAST_SPEC,
            dataset="CI_Forecast",
            conn=MagicMock(),
            retrieved_at=RETRIEVED_AT,
            request_context=REQUEST_CONTEXT,
            parse_rows=Mock(return_value=[]),
            load_rows=Mock(),
        )

    assert rows == original_rows
