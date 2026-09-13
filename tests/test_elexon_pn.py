import json
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from ingestion.elexon.contracts import PN_SPEC
from ingestion.elexon.pn_poller import load, parse
from ingestion.elexon.pn_poller import run as run_poller

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "elexon" / "pn_stream.json"
RETRIEVED_AT = datetime(2026, 8, 17, 7, 47, 13, tzinfo=timezone.utc)
WINDOW_START = datetime(2026, 8, 20, tzinfo=timezone.utc)
WINDOW_END = datetime(2026, 8, 21, tzinfo=timezone.utc)


@pytest.fixture
def source_rows():
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_parse_maps_a_source_row_to_the_database_tuple(source_rows):
    parsed_rows = parse(source_rows, RETRIEVED_AT)

    assert len(parsed_rows) == len(source_rows)
    assert parsed_rows[22] == (
        date(2026, 8, 21),
        31,
        datetime(2026, 8, 21, 14, 0, tzinfo=timezone.utc),
        datetime(2026, 8, 21, 14, 30, tzinfo=timezone.utc),
        250,
        474,
        "DRAXX-1",
        "T_DRAXX-1",
        RETRIEVED_AT,
    )


def test_load_uses_the_expected_columns_conflict_key_and_batch_size():
    parsed_rows = [Mock(name="parsed_row")]
    conn = MagicMock()

    with patch("ingestion.elexon.pn_poller.execute_values") as mock_execute_values:
        load(parsed_rows, conn)

    sql = "".join(mock_execute_values.call_args.args[1].split())
    assert sql == (
        "INSERTINTOraw.elexon_pn(settlement_date,settlement_period,time_from,"
        "time_to,level_from,level_to,national_grid_bm_unit,bm_unit,retrieved_at)"
        "VALUES%sONCONFLICT(national_grid_bm_unit,time_from,retrieved_at)DONOTHING"
    )
    assert mock_execute_values.call_args.args[2] == parsed_rows
    assert mock_execute_values.call_args.kwargs["page_size"] == 1000
    conn.commit.assert_called_once_with()


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
        patch("ingestion.elexon.pn_poller.fetch", return_value=response),
        patch("ingestion.elexon.pn_poller.process_rows") as mock_process_rows,
    ):
        with pytest.raises(RuntimeError, match="expected a non-empty list"):
            run_poller(conn, WINDOW_START, WINDOW_END)

    mock_process_rows.assert_not_called()


def test_run_passes_rows_and_request_context_to_shared_routing(source_rows):
    conn = Mock()

    with (
        patch("ingestion.elexon.pn_poller.fetch", return_value=source_rows),
        patch(
            "ingestion.elexon.pn_poller.process_rows",
            return_value=0,
        ) as mock_process_rows,
    ):
        run_poller(conn, WINDOW_START, WINDOW_END)

    retrieved_at = mock_process_rows.call_args.kwargs["retrieved_at"]
    mock_process_rows.assert_called_once_with(
        source_rows,
        spec=PN_SPEC,
        dataset="PN",
        conn=conn,
        retrieved_at=retrieved_at,
        request_context={
            "from": WINDOW_START.isoformat(),
            "to": WINDOW_END.isoformat(),
        },
        parse_rows=parse,
        load_rows=load,
    )


def test_run_fails_after_shared_routing_rejects_a_row(source_rows):
    conn = Mock()

    with (
        patch("ingestion.elexon.pn_poller.fetch", return_value=source_rows),
        patch("ingestion.elexon.pn_poller.process_rows", return_value=1),
    ):
        with pytest.raises(RuntimeError, match="Quarantined 1 row"):
            run_poller(conn, WINDOW_START, WINDOW_END)
