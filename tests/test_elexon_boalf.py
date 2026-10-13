from datetime import date, datetime, timezone

import pytest

from ingestion.elexon.boalf_poller import parse

RETRIEVED_AT = datetime(2026, 10, 7, 0, 0, 13, tzinfo=timezone.utc)
BOALF_COLUMNS = (
    "national_grid_bm_unit",
    "bm_unit",
    "acceptance_number",
    "acceptance_time",
    "settlement_date",
    "settlement_period_from",
    "settlement_period_to",
    "time_from",
    "time_to",
    "level_from",
    "level_to",
    "so_flag",
    "deemed_bo_flag",
    "stor_flag",
    "rr_flag",
    "amendment_flag",
    "retrieved_at",
)
UNFLAGGED_SOURCE_ROW = {
    "dataset": "BOALF",
    "settlementDate": "2026-10-06",
    "settlementPeriodFrom": 2,
    "settlementPeriodTo": 3,
    "timeFrom": "2026-10-05T23:30:00Z",
    "timeTo": "2026-10-06T00:05:00Z",
    "levelFrom": 21,
    "levelTo": 6,
    "acceptanceNumber": 1810,
    "acceptanceTime": "2026-10-05T22:46:00Z",
    "deemedBoFlag": False,
    "soFlag": False,
    "amendmentFlag": "ORI",
    "storFlag": False,
    "rrFlag": False,
    "nationalGridBmUnit": "LIONB-3",
    "bmUnit": "T_LIONB-3",
}


@pytest.mark.parametrize(
    "raised_flag,flag_column",
    [
        pytest.param("soFlag", "so_flag", id="soFlag"),
        pytest.param("deemedBoFlag", "deemed_bo_flag", id="deemedBoFlag"),
        pytest.param("storFlag", "stor_flag", id="storFlag"),
        pytest.param("rrFlag", "rr_flag", id="rrFlag"),
    ],
)
def test_parse_maps_each_source_field_to_its_own_column(raised_flag, flag_column):
    source_row = {**UNFLAGGED_SOURCE_ROW, raised_flag: True}

    (parsed_row,) = parse([source_row], RETRIEVED_AT)

    expected = {
        "national_grid_bm_unit": "LIONB-3",
        "bm_unit": "T_LIONB-3",
        "acceptance_number": 1810,
        "acceptance_time": datetime(2026, 10, 5, 22, 46, tzinfo=timezone.utc),
        "settlement_date": date(2026, 10, 6),
        "settlement_period_from": 2,
        "settlement_period_to": 3,
        "time_from": datetime(2026, 10, 5, 23, 30, tzinfo=timezone.utc),
        "time_to": datetime(2026, 10, 6, 0, 5, tzinfo=timezone.utc),
        "level_from": 21,
        "level_to": 6,
        "so_flag": False,
        "deemed_bo_flag": False,
        "stor_flag": False,
        "rr_flag": False,
        "amendment_flag": "ORI",
        "retrieved_at": RETRIEVED_AT,
    }
    expected[flag_column] = True
    assert dict(zip(BOALF_COLUMNS, parsed_row, strict=True)) == expected
