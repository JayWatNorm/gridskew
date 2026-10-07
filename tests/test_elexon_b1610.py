import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest
from fakes import connection_returning, cursor_of, single_spaced

from ingestion.elexon.b1610_poller import (
    british_day_window,
    capture_settlement_date,
    decimal_json_dumps,
    expected_period_count,
    fetch,
    load,
    parse,
    stored_coverage,
)
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


def settlement_date_and_end_time(parsed_rows, wanted_period):
    for parsed_row in parsed_rows:
        (
            _bm_unit,
            _national_grid_bm_unit_id,
            _psr_type,
            settlement_date,
            settlement_period,
            half_hour_end_time,
            _settlement_run_type,
            _quantity,
            _retrieved_at,
        ) = parsed_row
        if settlement_period == wanted_period:
            return settlement_date, half_hour_end_time
    raise AssertionError(f"No parsed row for period {wanted_period}")


def test_parse_preserves_the_settlement_date_at_the_utc_day_boundary(source_rows):
    parsed_rows = parse(source_rows, RETRIEVED_AT)

    settlement_date, end_time = settlement_date_and_end_time(parsed_rows, 1)
    assert settlement_date == end_time.date() + timedelta(days=1)

    settlement_date, end_time = settlement_date_and_end_time(parsed_rows, 2)
    assert settlement_date == end_time.date()


def test_load_uses_the_expected_columns_conflict_key_and_commits():
    parsed_rows = [Mock(name="parsed_row")]
    conn = MagicMock()

    with patch("ingestion.elexon.b1610_poller.execute_values") as mock_execute_values:
        load(parsed_rows, conn)

    _cursor, insert_sql, inserted_rows = mock_execute_values.call_args.args
    assert single_spaced(insert_sql) == (
        "INSERT INTO raw.elexon_b1610 (bm_unit, national_grid_bm_unit_id, psr_type, "
        "settlement_date, settlement_period, half_hour_end_time, settlement_run_type, "
        "quantity, retrieved_at) "
        "VALUES %s "
        "ON CONFLICT (bm_unit, settlement_date, settlement_period, "
        "settlement_run_type) DO NOTHING"
    )
    assert inserted_rows == parsed_rows
    conn.commit.assert_called_once_with()


def test_decimal_json_encoder_preserves_a_decimal_number():
    value = Decimal("123.456")

    decoded = json.loads(decimal_json_dumps(value), parse_float=Decimal)

    assert type(decoded) is Decimal
    assert decoded.as_tuple() == value.as_tuple()


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


def test_capture_settlement_date_fails_after_loading_when_a_period_is_missing():
    conn = Mock()
    rows = [{"settlementRunType": "R1"}]
    with (
        patch("ingestion.elexon.b1610_poller.run", return_value=rows) as mock_run,
        patch(
            "ingestion.elexon.b1610_poller.stored_coverage",
            return_value=(47, list(range(1, 48)), 1),
        ),
    ):
        with pytest.raises(RuntimeError, match="47 of 48 periods stored"):
            capture_settlement_date(conn, date(2026, 8, 10), "R1")

    mock_run.assert_called_once_with(
        conn,
        datetime(2026, 8, 9, 23, 0, tzinfo=timezone.utc),
        datetime(2026, 8, 10, 22, 30, tzinfo=timezone.utc),
        settlement_run_type="R1",
        expected_settlement_date=date(2026, 8, 10),
    )


def test_capture_settlement_date_fails_when_another_run_type_is_returned():
    rows = [{"settlementRunType": "R1"}, {"settlementRunType": "SF"}]
    with (
        patch("ingestion.elexon.b1610_poller.run", return_value=rows),
        patch(
            "ingestion.elexon.b1610_poller.stored_coverage",
            return_value=(96, list(range(1, 49)), 1),
        ),
    ):
        with pytest.raises(RuntimeError, match="returned run types"):
            capture_settlement_date(Mock(), date(2026, 8, 10), "R1")


def test_stored_coverage_is_scoped_to_date_and_run():
    coverage = (440496, list(range(1, 49)), 9177)
    conn = connection_returning(coverage)

    assert stored_coverage(conn, date(2026, 8, 10), "R1") == coverage

    sql, params = cursor_of(conn).execute.call_args.args
    assert "WHERE settlement_date = %s AND settlement_run_type = %s" in sql
    assert params == (date(2026, 8, 10), "R1")


def cohort_rows(source_rows):
    """Return the fixture rows relabelled as R1 rows for 2026-08-10."""

    rows = []
    for row in source_rows:
        rows.append(dict(row, settlementRunType="R1", settlementDate="2026-08-10"))
    return rows


def connection_checking_commit_before_coverage(coverage):
    """Return a fake connection that fails if coverage is read before a commit."""

    conn = connection_returning(coverage)

    def check_commit(*_args):
        conn.commit.assert_called_once_with()

    cursor_of(conn).execute.side_effect = check_commit
    return conn


def test_cohort_commits_valid_rows_then_fails_when_a_period_is_missing(
    source_rows, caplog
):
    rows = cohort_rows(source_rows)
    stored_periods = list(range(1, 48))
    conn = connection_checking_commit_before_coverage((len(rows), stored_periods, 1))

    with (
        patch("ingestion.elexon.b1610_poller.fetch", return_value=rows) as get_rows,
        patch("ingestion.elexon.b1610_poller.execute_values") as insert_rows,
        caplog.at_level("INFO", logger="ingestion.elexon.b1610_poller"),
    ):
        with pytest.raises(RuntimeError, match="47 of 48 periods stored"):
            capture_settlement_date(conn, date(2026, 8, 10), "R1")

    _from_date, _to_date, requested_run_type = get_rows.call_args.args
    assert requested_run_type == "R1"
    insert_rows.assert_called_once()
    conn.rollback.assert_called_once_with()
    assert '"stored_periods": 47' in caplog.text


def test_cohort_commits_valid_rows_then_reports_complete_coverage(source_rows, caplog):
    rows = cohort_rows(source_rows)
    stored_periods = list(range(1, 49))
    conn = connection_checking_commit_before_coverage((len(rows), stored_periods, 1))

    with (
        patch("ingestion.elexon.b1610_poller.fetch", return_value=rows) as get_rows,
        patch("ingestion.elexon.b1610_poller.execute_values") as insert_rows,
        caplog.at_level("INFO", logger="ingestion.elexon.b1610_poller"),
    ):
        summary = capture_settlement_date(conn, date(2026, 8, 10), "R1")

    assert summary["stored_periods"] == summary["expected_periods"] == 48
    assert summary["returned_run_types"] == {"R1": len(rows)}
    _from_date, _to_date, requested_run_type = get_rows.call_args.args
    assert requested_run_type == "R1"
    insert_rows.assert_called_once()
    conn.rollback.assert_called_once_with()
    assert '"stored_periods": 48' in caplog.text


def test_run_preserves_the_requested_run_type_in_quarantine_context(source_rows):
    with (
        patch("ingestion.elexon.b1610_poller.fetch", return_value=source_rows),
        patch("ingestion.elexon.b1610_poller.process_rows", return_value=0) as route,
    ):
        assert run_poller(Mock(), WINDOW_START, WINDOW_END, "R1") is source_rows
    assert route.call_args.kwargs["request_context"]["settlementRunType"] == "R1"


@pytest.mark.parametrize("stored_periods", [[], list(range(1, 49))])
def test_cohort_refuses_wrong_response_date_before_any_write(
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
def test_cohort_refuses_out_of_range_returned_period_before_writes(
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


CLOCK_CHANGE_DAYS = [
    pytest.param(date(2026, 3, 29), 46, id="spring"),
    pytest.param(date(2026, 10, 25), 50, id="autumn"),
]


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


@pytest.mark.parametrize("settlement_date,expected", CLOCK_CHANGE_DAYS)
def test_cohort_accepts_exactly_the_expected_stored_periods(
    source_rows, settlement_date, expected
):
    rows = one_row_per_period(source_rows, settlement_date, expected)
    stored_periods = list(range(1, expected + 1))
    conn = connection_returning((expected, stored_periods, 1))

    with (
        patch("ingestion.elexon.b1610_poller.fetch", return_value=rows),
        patch("ingestion.elexon.b1610_poller.execute_values"),
    ):
        summary = capture_settlement_date(conn, settlement_date, "R1")

    assert summary["stored_period_numbers"] == stored_periods
    conn.commit.assert_called_once_with()
    conn.rollback.assert_called_once_with()


def test_cohort_rejects_the_right_count_of_wrong_stored_periods(source_rows):
    settlement_date = date(2026, 8, 10)
    expected = 48
    rows = one_row_per_period(source_rows, settlement_date, expected)
    stored_periods = list(range(1, expected + 1))
    stored_periods[-1] = expected + 2
    conn = connection_returning((expected, stored_periods, 1))

    with (
        patch("ingestion.elexon.b1610_poller.fetch", return_value=rows),
        patch("ingestion.elexon.b1610_poller.execute_values"),
    ):
        with pytest.raises(RuntimeError, match="stored period numbers"):
            capture_settlement_date(conn, settlement_date, "R1")

    conn.commit.assert_called_once_with()
    conn.rollback.assert_called_once_with()
