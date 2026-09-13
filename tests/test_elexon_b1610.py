import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from ingestion.elexon.b1610_poller import decimal_json_dumps, load, parse
from ingestion.elexon.b1610_poller import run as run_poller
from ingestion.elexon.contracts import B1610_SPEC

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "elexon" / "b1610_stream.json"
RETRIEVED_AT = datetime(2026, 8, 17, 7, 47, 13, tzinfo=timezone.utc)
WINDOW_START = datetime(2026, 8, 20, tzinfo=timezone.utc)
WINDOW_END = datetime(2026, 8, 21, tzinfo=timezone.utc)


@pytest.fixture
def source_rows():
    return json.loads(
        FIXTURE_PATH.read_text(encoding="utf-8"),
        parse_float=Decimal,
    )


def test_parse_maps_a_source_row_to_the_database_tuple(source_rows):
    parsed_rows = parse(source_rows, RETRIEVED_AT)

    assert len(parsed_rows) == len(source_rows)
    assert parsed_rows[10] == (
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


def test_parse_preserves_the_settlement_date_at_the_utc_day_boundary(source_rows):
    parsed_rows = parse(source_rows, RETRIEVED_AT)
    period_one = next(row for row in parsed_rows if row[4] == 1)
    period_two = next(row for row in parsed_rows if row[4] == 2)

    assert period_one[3] == period_one[5].date() + timedelta(days=1)
    assert period_two[3] == period_two[5].date()


def test_load_uses_the_expected_columns_conflict_key_and_batch_size():
    parsed_rows = [Mock(name="parsed_row")]
    conn = MagicMock()

    with patch("ingestion.elexon.b1610_poller.execute_values") as mock_execute_values:
        load(parsed_rows, conn)

    sql = "".join(mock_execute_values.call_args.args[1].split())
    assert sql == (
        "INSERTINTOraw.elexon_b1610(bm_unit,national_grid_bm_unit_id,psr_type,"
        "settlement_date,settlement_period,half_hour_end_time,settlement_run_type,"
        "quantity,retrieved_at)VALUES%sONCONFLICT(bm_unit,settlement_date,"
        "settlement_period,settlement_run_type)DONOTHING"
    )
    assert mock_execute_values.call_args.args[2] == parsed_rows
    assert mock_execute_values.call_args.kwargs["page_size"] == 1000
    conn.commit.assert_called_once_with()


def test_decimal_json_encoder_preserves_a_decimal_number():
    value = Decimal("123.456")

    decoded = json.loads(decimal_json_dumps(value), parse_float=Decimal)

    assert type(decoded) is Decimal
    assert decoded.as_tuple() == value.as_tuple()


@pytest.mark.parametrize(
    "response",
    [
        pytest.param(None, id="none"),
        pytest.param({}, id="dictionary"),
        pytest.param([], id="empty-list"),
    ],
)
def test_run_rejects_an_invalid_response_envelope(response):
    conn = Mock()

    with (
        patch("ingestion.elexon.b1610_poller.fetch", return_value=response),
        patch("ingestion.elexon.b1610_poller.process_rows") as mock_process_rows,
    ):
        with pytest.raises(RuntimeError, match="expected a non-empty list"):
            run_poller(conn, WINDOW_START, WINDOW_END)

    mock_process_rows.assert_not_called()


def test_run_passes_rows_context_and_decimal_encoder_to_shared_routing(source_rows):
    conn = Mock()

    with (
        patch("ingestion.elexon.b1610_poller.fetch", return_value=source_rows),
        patch(
            "ingestion.elexon.b1610_poller.process_rows",
            return_value=0,
        ) as mock_process_rows,
    ):
        run_poller(conn, WINDOW_START, WINDOW_END)

    retrieved_at = mock_process_rows.call_args.kwargs["retrieved_at"]
    mock_process_rows.assert_called_once_with(
        source_rows,
        spec=B1610_SPEC,
        dataset="B1610",
        conn=conn,
        retrieved_at=retrieved_at,
        request_context={
            "from": WINDOW_START.isoformat(),
            "to": WINDOW_END.isoformat(),
        },
        parse_rows=parse,
        load_rows=load,
        payload_dumps=decimal_json_dumps,
    )


def test_run_fails_after_shared_routing_rejects_a_row(source_rows):
    conn = Mock()

    with (
        patch("ingestion.elexon.b1610_poller.fetch", return_value=source_rows),
        patch("ingestion.elexon.b1610_poller.process_rows", return_value=1),
    ):
        with pytest.raises(RuntimeError, match="Quarantined 1 row"):
            run_poller(conn, WINDOW_START, WINDOW_END)
