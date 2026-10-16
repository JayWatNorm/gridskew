from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import Mock, patch

import pytest

from ingestion.elexon import qas_poller
from ingestion.routing import ContractRejected

RETRIEVED_AT = datetime(2026, 10, 20, 0, 1, 7, tzinfo=timezone.utc)
SETTLEMENT_DATE = date(2026, 10, 1)
# British dates with 46 and 50 settlement periods.
SHORT_DAY = date(2026, 3, 29)
LONG_DAY = date(2026, 10, 25)
QAS_COLUMNS = (
    "settlement_date",
    "settlement_period",
    "national_grid_bm_unit",
    "bm_unit",
    "bm_unit_applicable_balancing_services_volume",
    "retrieved_at",
)


def source_row(settlement_date, period, unit, volume="1.250"):
    return {
        "settlementDate": settlement_date.isoformat(),
        "settlementPeriod": period,
        "bmUnitApplicableBalancingServicesVolume": Decimal(volume),
        "nationalGridBmUnit": unit,
        "bmUnit": f"T_{unit}",
        "time": "2026-10-01T00:00:00Z",
    }


def whole_day(settlement_date, period_count, units=("BATT-1", "BATT-2")):
    rows = []
    for period in range(1, period_count + 1):
        for unit in units:
            rows.append(source_row(settlement_date, period, unit))
    return rows


def response_of(rows):
    return {"metadata": {"datasets": ["QAS"]}, "data": rows}


def run_with_response(settlement_date, response, conn=None):
    """Run one capture against a fixed response; return the rows loaded."""

    with (
        patch.object(qas_poller, "fetch", return_value=response),
        patch.object(qas_poller, "load") as mock_load,
    ):
        qas_poller.run(conn or Mock(), settlement_date)
    if not mock_load.called:
        return None
    return mock_load.call_args.args[0]


def test_fetch_asks_by_settlement_date_with_both_period_parameters():
    response = Mock(
        text='{"data": [{"bmUnitApplicableBalancingServicesVolume": 1.25}]}'
    )

    with patch(
        "ingestion.elexon.qas_poller.requests.get", return_value=response
    ) as get:
        body = qas_poller.fetch(SETTLEMENT_DATE)

    assert get.call_args.kwargs["params"] == {
        "from": "2026-10-01",
        "to": "2026-10-01",
        "settlementPeriodFrom": 1,
        "settlementPeriodTo": 50,
        "format": "json",
    }
    assert get.call_args.args[0].endswith("/balancing/nonbm/volumes")
    assert body["data"][0]["bmUnitApplicableBalancingServicesVolume"] == Decimal("1.25")


def test_parse_maps_each_source_field_to_its_own_column():
    row = source_row(SETTLEMENT_DATE, 31, "BATT-7", volume="-0.044")

    (parsed_row,) = qas_poller.parse([row], RETRIEVED_AT)

    assert dict(zip(QAS_COLUMNS, parsed_row, strict=True)) == {
        "settlement_date": date(2026, 10, 1),
        "settlement_period": 31,
        "national_grid_bm_unit": "BATT-7",
        "bm_unit": "T_BATT-7",
        "bm_unit_applicable_balancing_services_volume": Decimal("-0.044"),
        "retrieved_at": RETRIEVED_AT,
    }


def test_a_whole_day_loads_every_row():
    loaded = run_with_response(
        SETTLEMENT_DATE, response_of(whole_day(SETTLEMENT_DATE, 48))
    )

    assert len(loaded) == 96


@pytest.mark.parametrize(
    "response",
    [
        pytest.param(response_of([]), id="no-rows"),
        pytest.param({"metadata": {}}, id="no-data"),
        pytest.param([], id="list"),
    ],
)
def test_an_empty_response_fails_the_capture(response):
    conn = Mock()

    with pytest.raises(RuntimeError, match="expected a non-empty list"):
        run_with_response(SETTLEMENT_DATE, response, conn)

    conn.cursor.assert_not_called()


def test_a_row_for_another_date_fails_before_any_write():
    rows = whole_day(SETTLEMENT_DATE, 48)
    rows.append(source_row(date(2026, 10, 2), 1, "BATT-1"))
    conn = Mock()

    with pytest.raises(RuntimeError, match="returned settlement date '2026-10-02'"):
        run_with_response(SETTLEMENT_DATE, response_of(rows), conn)

    conn.cursor.assert_not_called()


def test_a_46_period_day_loads_period_46():
    loaded = run_with_response(SHORT_DAY, response_of(whole_day(SHORT_DAY, 46)))

    assert len(loaded) == 92


def test_a_46_period_day_fails_on_period_47():
    rows = whole_day(SHORT_DAY, 46)
    rows.append(source_row(SHORT_DAY, 47, "BATT-1"))

    with pytest.raises(RuntimeError, match="settlement period 47; expected 1..46"):
        run_with_response(SHORT_DAY, response_of(rows))


def test_a_50_period_day_loads_period_50():
    loaded = run_with_response(LONG_DAY, response_of(whole_day(LONG_DAY, 50)))

    assert len(loaded) == 100


def test_period_0_fails():
    rows = whole_day(SETTLEMENT_DATE, 48)
    rows.append(source_row(SETTLEMENT_DATE, 0, "BATT-1"))

    with pytest.raises(RuntimeError, match="settlement period 0; expected 1..48"):
        run_with_response(SETTLEMENT_DATE, response_of(rows))


def test_two_rows_for_one_unit_and_period_fail_before_any_write():
    rows = whole_day(SETTLEMENT_DATE, 48)
    rows.append(source_row(SETTLEMENT_DATE, 12, "BATT-2", volume="9.000"))
    conn = Mock()

    with pytest.raises(RuntimeError, match="BATT-2 period 12"):
        run_with_response(SETTLEMENT_DATE, response_of(rows), conn)

    conn.cursor.assert_not_called()


def test_a_period_with_no_row_fails_before_any_write():
    rows = []
    for row in whole_day(SETTLEMENT_DATE, 48):
        if row["settlementPeriod"] != 17:
            rows.append(row)
    conn = Mock()

    with pytest.raises(RuntimeError, match=r"no rows for periods \[17\]"):
        run_with_response(SETTLEMENT_DATE, response_of(rows), conn)

    conn.cursor.assert_not_called()


@pytest.mark.parametrize(
    "bad_unit",
    [
        pytest.param(None, id="null"),
        # Unhashable: the duplicate check must leave it to the contract.
        pytest.param(["BATT-9"], id="list"),
    ],
)
def test_one_rejected_row_quarantines_it_and_loads_nothing(bad_unit):
    rows = whole_day(SETTLEMENT_DATE, 48)
    # The rejected row is the only row of period 3: it still counts as the
    # period's row, so the response reaches the contract and its quarantine.
    del rows[4]
    rows[4]["nationalGridBmUnit"] = bad_unit

    with (
        patch.object(qas_poller, "fetch", return_value=response_of(rows)),
        patch.object(qas_poller, "load") as mock_load,
        patch("ingestion.routing.quarantine_rows") as mock_quarantine,
    ):
        with pytest.raises(ContractRejected, match="Quarantined 1 row"):
            qas_poller.run(Mock(), SETTLEMENT_DATE)

    mock_load.assert_not_called()
    (rejected_finding,) = mock_quarantine.call_args.args[0]
    assert rejected_finding["index"] == 4
