from datetime import date, datetime, timezone

from ingestion.elexon.qpn_poller import parse

RETRIEVED_AT = datetime(2026, 8, 17, 7, 47, 13, tzinfo=timezone.utc)
QPN_COLUMNS = (
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
    "dataset": "QPN",
    "settlementDate": "2026-08-21",
    "settlementPeriod": 36,
    "timeFrom": "2026-08-21T16:30:00Z",
    "timeTo": "2026-08-21T17:00:00Z",
    "levelFrom": -60,
    "levelTo": -45,
    "nationalGridBmUnit": "WILCT-1",
    "bmUnit": "T_WILCT-1",
}


def test_parse_maps_each_source_field_to_its_own_column():
    (parsed_row,) = parse([SOURCE_ROW], RETRIEVED_AT)

    assert dict(zip(QPN_COLUMNS, parsed_row, strict=True)) == {
        "settlement_date": date(2026, 8, 21),
        "settlement_period": 36,
        "time_from": datetime(2026, 8, 21, 16, 30, tzinfo=timezone.utc),
        "time_to": datetime(2026, 8, 21, 17, 0, tzinfo=timezone.utc),
        "level_from": -60,
        "level_to": -45,
        "national_grid_bm_unit": "WILCT-1",
        "bm_unit": "T_WILCT-1",
        "retrieved_at": RETRIEVED_AT,
    }
