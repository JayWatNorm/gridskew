from datetime import date, datetime, timezone
from unittest.mock import MagicMock, Mock, patch

import pytest
from fakes import cursor_of, single_spaced
from test_elexon_boalf import BOALF_COLUMNS
from test_elexon_qpn import QPN_COLUMNS

from ingestion.elexon import boalf_poller, pn_poller, qpn_poller
from ingestion.elexon.contracts import BOALF_SPEC, PN_SPEC, QPN_SPEC
from ingestion.routing import ContractRejected

RETRIEVED_AT = datetime(2026, 8, 17, 7, 47, 13, tzinfo=timezone.utc)
WINDOW_START = datetime(2026, 8, 20, tzinfo=timezone.utc)
WINDOW_END = datetime(2026, 8, 21, tzinfo=timezone.utc)
PN_COLUMNS = (
    "settlement_date",
    "settlement_period",
    "time_from",
    "time_to",
    "level_from",
    "level_to",
    "national_grid_bm_unit",
    "bm_unit",
    "retrieved_at",
)
SOURCE_ROW = {
    "dataset": "PN",
    "settlementDate": "2026-08-21",
    "settlementPeriod": 31,
    "timeFrom": "2026-08-21T14:00:00Z",
    "timeTo": "2026-08-21T14:30:00Z",
    "levelFrom": 250,
    "levelTo": 474,
    "nationalGridBmUnit": "DRAXX-1",
    "bmUnit": "T_DRAXX-1",
}

POLLERS = [
    pytest.param(pn_poller, id="PN"),
    pytest.param(qpn_poller, id="QPN"),
    pytest.param(boalf_poller, id="BOALF"),
]


def test_parse_maps_each_source_field_to_its_own_column():
    (parsed_row,) = pn_poller.parse([SOURCE_ROW], RETRIEVED_AT)

    assert dict(zip(PN_COLUMNS, parsed_row, strict=True)) == {
        "settlement_date": date(2026, 8, 21),
        "settlement_period": 31,
        "time_from": datetime(2026, 8, 21, 14, 0, tzinfo=timezone.utc),
        "time_to": datetime(2026, 8, 21, 14, 30, tzinfo=timezone.utc),
        "level_from": 250,
        "level_to": 474,
        "national_grid_bm_unit": "DRAXX-1",
        "bm_unit": "T_DRAXX-1",
        "retrieved_at": RETRIEVED_AT,
    }


PN_CONFLICT_KEY = "national_grid_bm_unit, time_from, retrieved_at"
BOALF_CONFLICT_KEY = "national_grid_bm_unit, acceptance_number, time_from, retrieved_at"


@pytest.mark.parametrize(
    "poller,table,columns,conflict_key",
    [
        pytest.param(pn_poller, "raw.elexon_pn", PN_COLUMNS, PN_CONFLICT_KEY, id="PN"),
        pytest.param(
            qpn_poller, "raw.elexon_qpn", QPN_COLUMNS, PN_CONFLICT_KEY, id="QPN"
        ),
        pytest.param(
            boalf_poller,
            "raw.elexon_boalf",
            BOALF_COLUMNS,
            BOALF_CONFLICT_KEY,
            id="BOALF",
        ),
    ],
)
def test_load_inserts_the_rows_into_its_own_table_and_commits(
    poller, table, columns, conflict_key
):
    parsed_rows = [Mock(name="parsed_row")]
    conn = MagicMock()

    with patch.object(poller, "execute_values") as mock_execute_values:
        poller.load(parsed_rows, conn)

    cursor, insert_sql, inserted_rows = mock_execute_values.call_args.args
    assert cursor is cursor_of(conn)
    # The columns in the order parse builds each tuple, which the parse tests
    # check by name: a reordered column list would load values into the
    # wrong columns of the same type. A repeated capture is ignored.
    assert single_spaced(insert_sql) == (
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES %s "
        f"ON CONFLICT ({conflict_key}) DO NOTHING"
    )
    assert inserted_rows == parsed_rows
    conn.commit.assert_called_once_with()


@pytest.mark.parametrize(
    "response",
    [
        pytest.param({"data": []}, id="dictionary"),
        pytest.param([], id="empty-list"),
    ],
)
@pytest.mark.parametrize("poller", POLLERS)
def test_run_rejects_an_invalid_response_envelope(poller, response):
    conn = Mock()

    with (
        patch.object(poller, "fetch", return_value=response),
        patch.object(poller, "process_rows") as mock_process_rows,
    ):
        with pytest.raises(RuntimeError, match="expected a non-empty list"):
            poller.run(conn, WINDOW_START, WINDOW_END)

    mock_process_rows.assert_not_called()


@pytest.mark.parametrize(
    "poller,spec,dataset",
    [
        pytest.param(pn_poller, PN_SPEC, "PN", id="PN"),
        pytest.param(qpn_poller, QPN_SPEC, "QPN", id="QPN"),
        pytest.param(boalf_poller, BOALF_SPEC, "BOALF", id="BOALF"),
    ],
)
def test_run_routes_rows_through_its_own_parse_load_and_dataset_label(
    poller, spec, dataset
):
    source_rows = [Mock(name="source_row")]

    with (
        patch.object(poller, "fetch", return_value=source_rows),
        patch.object(poller, "process_rows", return_value=0) as mock_process_rows,
    ):
        poller.run(Mock(), WINDOW_START, WINDOW_END)

    routed = mock_process_rows.call_args
    assert routed.args == (source_rows,)
    assert routed.kwargs["parse_rows"] is poller.parse
    assert routed.kwargs["load_rows"] is poller.load
    assert routed.kwargs["spec"] is spec
    assert routed.kwargs["dataset"] == dataset


@pytest.mark.parametrize("poller", POLLERS)
def test_run_fails_after_shared_routing_rejects_a_row(poller):
    conn = Mock()

    with (
        patch.object(poller, "fetch", return_value=[Mock(name="source_row")]),
        patch.object(poller, "process_rows", return_value=1),
    ):
        with pytest.raises(ContractRejected, match="Quarantined 1 row"):
            poller.run(conn, WINDOW_START, WINDOW_END)
