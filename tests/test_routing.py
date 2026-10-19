import json
import logging
from copy import deepcopy
from datetime import datetime, timezone
from unittest.mock import MagicMock, Mock, patch

import pytest

from ingestion.carbon_intensity.contracts import FORECAST_SPEC
from ingestion.routing import process_rows

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
    parse_rows = Mock(return_value=parsed_rows)
    load_rows = Mock()

    with patch("ingestion.routing.quarantine_rows") as mock_quarantine:
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
    assert parse_rows.call_args.args[0] == [valid_row, warning_row]
    assert load_rows.call_args.args[0] is parsed_rows
    (rejected_finding,) = mock_quarantine.call_args.args[0]
    assert rejected_finding["index"] == 2
    assert rejected_finding["row"] == rejected_row


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

    payloads_by_field = {}
    for record in caplog.records:
        payload = json.loads(record.getMessage())
        assert payload["dataset"] == "CI_Forecast"
        payloads_by_field[payload["field"]] = payload

    assert len(caplog.records) == 2
    assert len(payloads_by_field) == 2
    note_warning = payloads_by_field["publisherNote"]
    assert note_warning["affected_row_count"] == 6
    assert note_warning["sample_source_indexes"] == [0, 1, 2, 3, 4]
    status_warning = payloads_by_field["intensity.publisherStatus"]
    assert status_warning["sample_source_indexes"] == [5]


def test_process_rows_can_leave_missing_optional_fields_unlogged(caplog):
    optional_field = {"type": str, "required": False, "nullable": True}
    spec = dict(FORECAST_SPEC, publisherNote=optional_field)
    row = make_source_row()
    row["unexpectedField"] = "observed"

    caplog.set_level(logging.WARNING, logger="ingestion.routing")
    process_rows(
        [row],
        spec=spec,
        dataset="CI_Forecast",
        conn=Mock(),
        retrieved_at=RETRIEVED_AT,
        request_context=REQUEST_CONTEXT,
        parse_rows=Mock(return_value=object()),
        load_rows=Mock(),
        log_missing_optional_fields=False,
    )

    logged_reasons = []
    for record in caplog.records:
        logged_reasons.append(json.loads(record.getMessage())["reason"])
    assert logged_reasons == ["Unexpected field"]


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

    with patch("ingestion.routing.insert_rows"):
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


def test_process_rows_loads_nothing_from_a_batch_with_a_rejected_row_when_asked():
    valid_row = make_source_row()
    rejected_row = deepcopy(valid_row)
    del rejected_row["intensity"]["forecast"]
    parse_rows = Mock()
    load_rows = Mock()

    with patch("ingestion.routing.quarantine_rows") as mock_quarantine:
        rejected_count = process_rows(
            [valid_row, rejected_row],
            spec=FORECAST_SPEC,
            dataset="CI_Forecast",
            conn=Mock(),
            retrieved_at=RETRIEVED_AT,
            request_context=REQUEST_CONTEXT,
            parse_rows=parse_rows,
            load_rows=load_rows,
            load_all_or_none=True,
        )

    assert rejected_count == 1
    (rejected_finding,) = mock_quarantine.call_args.args[0]
    assert rejected_finding["row"] == rejected_row
    parse_rows.assert_not_called()
    load_rows.assert_not_called()
