import json
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, Mock, patch

import pytest

from ingestion.elexon import (
    boalf_poller,
    physical_notifications,
    pn_poller,
    qpn_poller,
)
from ingestion.elexon.contracts import BOALF_SPEC, PN_SPEC, QPN_SPEC
from ingestion.routing import ContractRejected
from tests.fakes import cursor_of

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

# Each entry point, and the module whose fetch and routing it runs.
POLLERS = [
    pytest.param(pn_poller, physical_notifications, id="PN"),
    pytest.param(qpn_poller, physical_notifications, id="QPN"),
    pytest.param(boalf_poller, boalf_poller, id="BOALF"),
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


@pytest.mark.parametrize(
    "response",
    [
        pytest.param({"data": []}, id="dictionary"),
        pytest.param([], id="empty-list"),
    ],
)
@pytest.mark.parametrize("poller,worker", POLLERS)
def test_run_rejects_an_invalid_response_envelope(poller, worker, response):
    conn = Mock()

    with (
        patch.object(worker, "fetch", return_value=response),
        patch.object(worker, "process_rows") as mock_process_rows,
    ):
        with pytest.raises(RuntimeError, match="expected a non-empty list"):
            poller.run(conn, WINDOW_START, WINDOW_END)

    mock_process_rows.assert_not_called()


@pytest.mark.parametrize(
    "poller,worker,spec,dataset",
    [
        pytest.param(pn_poller, physical_notifications, PN_SPEC, "PN", id="PN"),
        pytest.param(qpn_poller, physical_notifications, QPN_SPEC, "QPN", id="QPN"),
        pytest.param(boalf_poller, boalf_poller, BOALF_SPEC, "BOALF", id="BOALF"),
    ],
)
def test_run_routes_rows_through_its_own_parse_load_and_dataset_label(
    poller, worker, spec, dataset
):
    source_rows = [Mock(name="source_row")]

    with (
        patch.object(worker, "fetch", return_value=source_rows),
        patch.object(worker, "process_rows", return_value=0) as mock_process_rows,
    ):
        poller.run(Mock(), WINDOW_START, WINDOW_END)

    routed = mock_process_rows.call_args
    assert routed.args == (source_rows,)
    assert routed.kwargs["parse_rows"] is poller.parse
    assert routed.kwargs["load_rows"] is poller.load
    assert routed.kwargs["spec"] is spec
    assert routed.kwargs["dataset"] == dataset


@pytest.mark.parametrize("poller,worker", POLLERS)
def test_run_fails_after_shared_routing_rejects_a_row(poller, worker):
    conn = Mock()

    with (
        patch.object(worker, "fetch", return_value=[Mock(name="source_row")]),
        patch.object(worker, "process_rows", return_value=1),
    ):
        with pytest.raises(ContractRejected, match="Quarantined 1 row"):
            poller.run(conn, WINDOW_START, WINDOW_END)


def test_load_warns_with_the_number_of_rows_the_key_skipped(caplog):
    zero_length_row = {**SOURCE_ROW, "timeTo": SOURCE_ROW["timeFrom"]}
    ramp_down_row = {**SOURCE_ROW, "levelFrom": 474, "levelTo": 250}
    parsed_rows = pn_poller.parse(
        [SOURCE_ROW, zero_length_row, ramp_down_row], RETRIEVED_AT
    )
    conn = MagicMock()
    cursor_of(conn).rowcount = 1

    with caplog.at_level("WARNING", logger="ingestion.elexon.physical_notifications"):
        pn_poller.load(parsed_rows, conn)

    (warning,) = caplog.records
    assert json.loads(warning.getMessage()) == {
        "dataset": "PN",
        "severity": "warning",
        "reason": "Rows share a unit and start time within one capture",
        "rows_sent": 3,
        "rows_inserted": 1,
        "rows_skipped": 2,
    }
    conn.commit.assert_called_once()
