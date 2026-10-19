import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest
from fakes import connection_returning

from ingestion.elexon.b1610_poller import (
    british_day_window,
    capture_settlement_date,
    decimal_json_dumps,
    expected_period_count,
    fetch,
    parse,
    replaced_by_a_later_run,
    run_in_force,
)
from ingestion.elexon.b1610_poller import run as run_poller
from ingestion.routing import ContractRejected

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


def parsed_row_for(parsed_rows, wanted_bm_unit, wanted_period):
    for parsed_row in parsed_rows:
        (
            bm_unit,
            _national_grid_bm_unit_id,
            _psr_type,
            _settlement_date,
            settlement_period,
            _half_hour_end_time,
            _settlement_run_type,
            _quantity,
            _retrieved_at,
        ) = parsed_row
        if bm_unit == wanted_bm_unit and settlement_period == wanted_period:
            return parsed_row
    raise AssertionError(f"No parsed row for {wanted_bm_unit} period {wanted_period}")


def test_parse_maps_a_source_row_to_the_database_tuple(source_rows):
    parsed_rows = parse(source_rows, RETRIEVED_AT)

    assert len(parsed_rows) == len(source_rows)
    # Period 1 ends on the previous UTC day: the settlement date comes from
    # the source, never from the end time.
    assert parsed_row_for(parsed_rows, "E__MDRX001", 1) == (
        "E__MDRX001",
        "DRAXD-1",
        "Generation",
        date(2026, 8, 9),
        1,
        datetime(2026, 8, 8, 23, 30, tzinfo=timezone.utc),
        "II",
        Decimal("-14.456"),
        RETRIEVED_AT,
    )


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
        patch("ingestion.elexon.b1610_poller.fetch", return_value=response),
        patch("ingestion.elexon.b1610_poller.process_rows") as mock_process_rows,
    ):
        with pytest.raises(RuntimeError, match="expected a non-empty list"):
            run_poller(conn, WINDOW_START, WINDOW_END)

    mock_process_rows.assert_not_called()


def test_run_passes_rows_context_and_decimal_encoder_to_shared_routing(source_rows):
    with (
        patch("ingestion.elexon.b1610_poller.fetch", return_value=source_rows),
        patch(
            "ingestion.elexon.b1610_poller.process_rows",
            return_value=0,
        ) as mock_process_rows,
    ):
        run_poller(Mock(), WINDOW_START, WINDOW_END, "R1")

    routed = mock_process_rows.call_args
    assert routed.args == (source_rows,)
    assert routed.kwargs["dataset"] == "B1610"
    assert routed.kwargs["payload_dumps"] is decimal_json_dumps
    assert routed.kwargs["request_context"] == {
        "from": WINDOW_START.isoformat(),
        "to": WINDOW_END.isoformat(),
        "settlementRunType": "R1",
    }


def test_run_fails_after_shared_routing_rejects_a_row(source_rows):
    conn = Mock()

    with (
        patch("ingestion.elexon.b1610_poller.fetch", return_value=source_rows),
        patch("ingestion.elexon.b1610_poller.process_rows", return_value=1),
    ):
        with pytest.raises(ContractRejected, match="Quarantined 1 row"):
            run_poller(conn, WINDOW_START, WINDOW_END)


@pytest.mark.parametrize(
    "settlement_date,expected",
    [
        pytest.param(
            date(2026, 8, 10),
            (
                datetime(2026, 8, 9, 23, 0, tzinfo=timezone.utc),
                datetime(2026, 8, 10, 22, 30, tzinfo=timezone.utc),
            ),
            id="british-summer-time",
        ),
        pytest.param(
            date(2026, 1, 15),
            (
                datetime(2026, 1, 15, 0, 0, tzinfo=timezone.utc),
                datetime(2026, 1, 15, 23, 30, tzinfo=timezone.utc),
            ),
            id="greenwich-mean-time",
        ),
    ],
)
def test_british_day_window_covers_one_settlement_date(settlement_date, expected):
    assert british_day_window(settlement_date) == expected


@pytest.mark.parametrize(
    "settlement_date,count",
    [
        pytest.param(date(2026, 3, 29), 46, id="spring"),
        pytest.param(date(2026, 8, 10), 48, id="normal"),
        pytest.param(date(2026, 10, 25), 50, id="autumn"),
    ],
)
def test_expected_period_count_follows_uk_clock_changes(settlement_date, count):
    assert expected_period_count(settlement_date) == count


def test_fetch_sends_the_settlement_run_type_only_when_requested():
    response = Mock(text="[]")
    with patch(
        "ingestion.elexon.b1610_poller.requests.get", return_value=response
    ) as mock_get:
        fetch(WINDOW_START, WINDOW_END, "R1")
        fetch(WINDOW_START, WINDOW_END)

    assert mock_get.call_args_list[0].kwargs["params"]["settlementRunType"] == "R1"
    assert "settlementRunType" not in mock_get.call_args_list[1].kwargs["params"]


NORMAL_DAY = date(2026, 8, 10)
ALL_48_PERIODS = list(range(1, 49))


def one_row_per_period(source_rows, settlement_date, period_count):
    """Return one R1 row for each period 1..period_count of the date."""

    rows = []
    for period in range(1, period_count + 1):
        rows.append(
            dict(
                source_rows[0],
                settlementDate=settlement_date.isoformat(),
                settlementRunType="R1",
                settlementPeriod=period,
            )
        )
    return rows


def capture(conn, settlement_date, rows, recent_units=None):
    """Capture R1 for the date from a response of `rows`, with the inserts
    stubbed and `recent_units` as the most units of the seven dates before.

    Return the summary and the arguments the capture fetched with.
    """

    with (
        patch("ingestion.elexon.b1610_poller.fetch", return_value=rows) as get_rows,
        patch("ingestion.elexon.b1610_poller.insert_rows"),
        patch(
            "ingestion.elexon.b1610_poller.recent_unit_count",
            return_value=recent_units,
        ),
    ):
        summary = capture_settlement_date(conn, settlement_date, "R1")
    return summary, get_rows.call_args.args


def test_capture_fails_when_another_run_type_is_returned(source_rows):
    rows = one_row_per_period(source_rows, NORMAL_DAY, 48)
    rows[0]["settlementRunType"] = "SF"
    conn = connection_returning((48, ALL_48_PERIODS, 1))

    with pytest.raises(RuntimeError, match="returned run types"):
        capture(conn, NORMAL_DAY, rows)


def test_capture_fails_with_fewer_than_95_percent_of_recent_units(source_rows):
    rows = one_row_per_period(source_rows, NORMAL_DAY, 48)
    conn = connection_returning((48, ALL_48_PERIODS, 9499))

    with pytest.raises(RuntimeError, match="stored 9499 units; the most in the"):
        capture(conn, NORMAL_DAY, rows, recent_units=10000)


def test_capture_accepts_exactly_95_percent_of_recent_units(source_rows):
    rows = one_row_per_period(source_rows, NORMAL_DAY, 48)
    conn = connection_returning((48, ALL_48_PERIODS, 9500))

    summary, _ = capture(conn, NORMAL_DAY, rows, recent_units=10000)

    assert summary["most_units_in_previous_seven_dates"] == 10000


def test_capture_without_an_earlier_date_checks_periods_only(source_rows):
    rows = one_row_per_period(source_rows, NORMAL_DAY, 48)
    conn = connection_returning((48, ALL_48_PERIODS, 1))

    summary, _ = capture(conn, NORMAL_DAY, rows, recent_units=None)

    assert summary["stored_units"] == 1


@pytest.mark.parametrize("stored_periods", [[], list(range(1, 49))])
def test_capture_refuses_wrong_response_date_before_any_write(
    source_rows, stored_periods
):
    rows = [
        dict(source_rows[0], settlementDate="2026-08-10", settlementRunType="R1"),
        dict(source_rows[0], settlementDate="2026-08-11", settlementRunType="R1"),
    ]
    conn = connection_returning((440496, stored_periods, 9177))
    with (
        patch("ingestion.elexon.b1610_poller.fetch", return_value=rows),
        patch("ingestion.elexon.b1610_poller.process_rows", return_value=0) as route,
    ):
        with pytest.raises(RuntimeError, match="returned settlement date"):
            capture_settlement_date(conn, date(2026, 8, 10), "R1")
    route.assert_not_called()
    conn.commit.assert_not_called()
    conn.cursor.assert_not_called()


@pytest.mark.parametrize(
    "settlement_date,period",
    [
        (date(2026, 8, 10), 0),
        (date(2026, 8, 10), 49),
        (date(2026, 3, 29), 47),
        (date(2026, 10, 25), 51),
    ],
)
def test_capture_refuses_out_of_range_returned_period_before_writes(
    source_rows, settlement_date, period
):
    rows = [
        dict(
            source_rows[0],
            settlementDate=settlement_date.isoformat(),
            settlementRunType="R1",
            settlementPeriod=period,
        )
    ]
    conn = MagicMock()
    with (
        patch("ingestion.elexon.b1610_poller.fetch", return_value=rows),
        patch("ingestion.elexon.b1610_poller.process_rows", return_value=0) as route,
    ):
        with pytest.raises(RuntimeError, match="returned settlement period"):
            capture_settlement_date(conn, settlement_date, "R1")
    route.assert_not_called()
    conn.commit.assert_not_called()


STORED_PERIOD_DAYS = [
    pytest.param(date(2026, 3, 29), 46, id="spring"),
    pytest.param(date(2026, 8, 10), 48, id="normal"),
    pytest.param(date(2026, 10, 25), 50, id="autumn"),
]


@pytest.mark.parametrize("settlement_date,expected", STORED_PERIOD_DAYS)
def test_capture_accepts_exactly_the_expected_stored_periods(
    source_rows, settlement_date, expected
):
    rows = one_row_per_period(source_rows, settlement_date, expected)
    stored_periods = list(range(1, expected + 1))
    conn = connection_returning((expected, stored_periods, 1))

    summary, fetched_with = capture(conn, settlement_date, rows)

    assert fetched_with == (*british_day_window(settlement_date), "R1")
    assert summary["stored_period_numbers"] == stored_periods
    conn.commit.assert_called_once_with()


def test_capture_rejects_the_right_count_of_wrong_stored_periods(source_rows):
    rows = one_row_per_period(source_rows, NORMAL_DAY, 48)
    stored_periods = list(range(1, 48)) + [50]
    conn = connection_returning((48, stored_periods, 1))

    with pytest.raises(RuntimeError, match=r"missing \[48\], unexpected \[50\]"):
        capture(conn, NORMAL_DAY, rows)

    conn.commit.assert_called_once_with()


def test_run_in_force_is_the_latest_run_any_unit_carries():
    rows_with_one_unit_still_at_ii = [
        {"settlementRunType": "II"},
        {"settlementRunType": "R3"},
        {"settlementRunType": "R3"},
    ]
    response = Mock()
    response.json.return_value = {"data": rows_with_one_unit_still_at_ii}
    with patch(
        "ingestion.elexon.b1610_poller.requests.get", return_value=response
    ) as mock_get:
        in_force = run_in_force(date(2025, 8, 25))

    assert in_force == "R3"
    assert mock_get.call_args.kwargs["params"]["settlementDate"] == "2025-08-25"


def test_run_in_force_is_none_before_the_date_is_published():
    response = Mock()
    response.json.return_value = {"data": []}
    with patch("ingestion.elexon.b1610_poller.requests.get", return_value=response):
        assert run_in_force(date(2026, 10, 8)) is None


def test_a_later_run_in_force_means_the_wanted_run_is_replaced():
    with patch("ingestion.elexon.b1610_poller.run_in_force", return_value="R1"):
        assert replaced_by_a_later_run(date(2026, 8, 12), "SF") == "R1"


def test_a_dispute_final_run_replaces_rf():
    with patch("ingestion.elexon.b1610_poller.run_in_force", return_value="DF"):
        assert replaced_by_a_later_run(date(2024, 6, 3), "RF") == "DF"


@pytest.mark.parametrize("in_force", [None, "II", "SF", "XX"])
def test_the_wanted_run_is_not_replaced_by_itself_an_earlier_or_an_unknown_run(
    in_force,
):
    with patch("ingestion.elexon.b1610_poller.run_in_force", return_value=in_force):
        assert replaced_by_a_later_run(date(2026, 9, 5), "SF") is None
