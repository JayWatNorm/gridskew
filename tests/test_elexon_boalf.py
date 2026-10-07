import json
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest
from fakes import single_spaced

from ingestion.elexon.boalf_poller import load, parse
from ingestion.elexon.boalf_poller import run as run_poller
from ingestion.elexon.contracts import BOALF_SPEC

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "elexon" / "boalf_stream.json"
RETRIEVED_AT = datetime(2026, 10, 7, 0, 0, 13, tzinfo=timezone.utc)
WINDOW_START = datetime(2026, 10, 5, tzinfo=timezone.utc)
WINDOW_END = datetime(2026, 10, 6, tzinfo=timezone.utc)
SYSTEM_FLAGGED_ROW = 0
FALLING_RAMP_ROW = 5


@pytest.fixture
def source_rows():
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_parse_maps_a_ramp_to_the_database_tuple(source_rows):
    parsed_rows = parse(source_rows, RETRIEVED_AT)

    assert len(parsed_rows) == len(source_rows)
    assert parsed_rows[FALLING_RAMP_ROW] == (
        "LIONB-3",
        "T_LIONB-3",
        1810,
        datetime(2026, 10, 5, 22, 46, tzinfo=timezone.utc),
        date(2026, 10, 6),
        1,
        1,
        datetime(2026, 10, 5, 23, 0, tzinfo=timezone.utc),
        datetime(2026, 10, 5, 23, 1, tzinfo=timezone.utc),
        21,
        0,
        False,
        False,
        False,
        False,
        "ORI",
        RETRIEVED_AT,
    )


def test_parse_reads_the_system_flag_from_its_own_field(source_rows):
    parsed_rows = parse(source_rows, RETRIEVED_AT)

    assert parsed_rows[SYSTEM_FLAGGED_ROW] == (
        "PEHE-1",
        "T_PEHE-1",
        186869,
        datetime(2026, 10, 5, 23, 4, tzinfo=timezone.utc),
        date(2026, 10, 6),
        3,
        4,
        datetime(2026, 10, 6, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 10, 6, 0, 30, tzinfo=timezone.utc),
        218,
        218,
        True,
        False,
        False,
        False,
        "ORI",
        RETRIEVED_AT,
    )


def test_load_uses_the_expected_columns_conflict_key_and_commits():
    parsed_rows = [Mock(name="parsed_row")]
    conn = MagicMock()

    with patch("ingestion.elexon.boalf_poller.execute_values") as mock_execute_values:
        load(parsed_rows, conn)

    _cursor, insert_sql, inserted_rows = mock_execute_values.call_args.args
    assert single_spaced(insert_sql) == (
        "INSERT INTO raw.elexon_boalf (national_grid_bm_unit, bm_unit, "
        "acceptance_number, acceptance_time, settlement_date, "
        "settlement_period_from, settlement_period_to, time_from, time_to, "
        "level_from, level_to, so_flag, deemed_bo_flag, stor_flag, rr_flag, "
        "amendment_flag, retrieved_at) "
        "VALUES %s "
        "ON CONFLICT (national_grid_bm_unit, acceptance_number, time_from, "
        "retrieved_at) DO NOTHING"
    )
    assert inserted_rows == parsed_rows
    conn.commit.assert_called_once_with()


@pytest.mark.parametrize(
    "response",
    [
        pytest.param({}, id="dictionary"),
        pytest.param([], id="empty-list"),
    ],
)
def test_run_rejects_an_invalid_response_envelope(response):
    conn = Mock()

    with (
        patch("ingestion.elexon.boalf_poller.fetch", return_value=response),
        patch("ingestion.elexon.boalf_poller.process_rows") as mock_process_rows,
    ):
        with pytest.raises(RuntimeError, match="BOALF API: expected a non-empty list"):
            run_poller(conn, WINDOW_START, WINDOW_END)

    mock_process_rows.assert_not_called()


def test_run_passes_rows_and_request_context_to_shared_routing(source_rows):
    conn = Mock()

    with (
        patch("ingestion.elexon.boalf_poller.fetch", return_value=source_rows),
        patch(
            "ingestion.elexon.boalf_poller.process_rows",
            return_value=0,
        ) as mock_process_rows,
    ):
        run_poller(conn, WINDOW_START, WINDOW_END)

    retrieved_at = mock_process_rows.call_args.kwargs["retrieved_at"]
    mock_process_rows.assert_called_once_with(
        source_rows,
        spec=BOALF_SPEC,
        dataset="BOALF",
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
        patch("ingestion.elexon.boalf_poller.fetch", return_value=source_rows),
        patch("ingestion.elexon.boalf_poller.process_rows", return_value=1),
    ):
        with pytest.raises(RuntimeError, match="Quarantined 1 row"):
            run_poller(conn, WINDOW_START, WINDOW_END)
