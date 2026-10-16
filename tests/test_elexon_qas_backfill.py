from datetime import date
from unittest.mock import MagicMock, patch

from fakes import cursor_of

from ingestion.elexon import qas_backfill

AUGUST_22 = date(2025, 8, 22)
AUGUST_23 = date(2025, 8, 23)
AUGUST_24 = date(2025, 8, 24)


def connection_holding(captured_dates):
    """A fake connection whose stored QAS dates are captured_dates."""

    conn = MagicMock()
    rows = []
    for captured in captured_dates:
        rows.append((captured,))
    cursor_of(conn).fetchall.return_value = rows
    return conn


def summary_of(settlement_date, rows, fewest):
    return {
        "settlement_date": settlement_date.isoformat(),
        "rows": rows,
        "units": 3,
        "periods": 48,
        "fewest_rows_in_a_period": fewest,
    }


def captured_in_turn(*outcomes):
    """Stand in for qas_poller.run: each call returns or raises the next outcome."""

    return patch("ingestion.elexon.qas_poller.run", side_effect=list(outcomes))


def test_the_backfill_covers_every_date_from_the_first_to_the_last():
    dates = qas_backfill.backfill_dates()

    assert dates[0] == date(2025, 8, 22)
    assert dates[-1] == date(2026, 10, 5)
    assert len(dates) == 410
    assert len(set(dates)) == 410


def test_a_captured_date_is_skipped_and_the_others_captured_in_order():
    conn = connection_holding([AUGUST_23])

    with captured_in_turn(
        summary_of(AUGUST_22, 50, 1), summary_of(AUGUST_24, 60, 2)
    ) as mock_run:
        report = qas_backfill.backfill(
            conn, [AUGUST_22, AUGUST_23, AUGUST_24], pause_seconds=0
        )

    captured = []
    for call in mock_run.call_args_list:
        captured.append(call.args[1])
    assert captured == [AUGUST_22, AUGUST_24]
    assert report["skipped_as_captured"] == 1


def test_a_failed_date_does_not_stop_the_dates_after_it():
    conn = connection_holding([])

    with captured_in_turn(
        RuntimeError("Invalid response: no rows for periods [17]"),
        summary_of(AUGUST_23, 60, 2),
    ):
        report = qas_backfill.backfill(conn, [AUGUST_22, AUGUST_23], pause_seconds=0)

    assert report["failed"] == {
        "2025-08-22": "Invalid response: no rows for periods [17]"
    }
    assert report["captured"] == 1
    conn.rollback.assert_called()


def test_the_report_names_the_smallest_day_and_the_fewest_rows_in_a_period():
    conn = connection_holding([])

    with captured_in_turn(
        summary_of(AUGUST_22, 5571, 90),
        summary_of(AUGUST_23, 4416, 101),
        summary_of(AUGUST_24, 4800, 87),
    ):
        report = qas_backfill.backfill(
            conn, [AUGUST_22, AUGUST_23, AUGUST_24], pause_seconds=0
        )

    assert report["total_rows"] == 14787
    assert report["smallest_day"] == {"settlement_date": "2025-08-23", "rows": 4416}
    assert report["fewest_rows_in_a_period"] == {
        "settlement_date": "2025-08-24",
        "rows": 87,
    }


def test_a_dry_run_makes_no_request():
    conn = connection_holding([AUGUST_23])

    with patch("ingestion.elexon.qas_poller.run") as mock_run:
        report = qas_backfill.plan(conn, [AUGUST_22, AUGUST_23, AUGUST_24])

    mock_run.assert_not_called()
    assert report == {
        "first_date": "2025-08-22",
        "last_date": "2025-08-24",
        "dates": 3,
        "already_captured": 1,
        "requests_to_make": 2,
    }


def test_the_exit_code_is_non_zero_when_any_date_failed():
    assert qas_backfill.exit_code({"failed": {"2025-08-22": "error"}}) == 1


def test_the_exit_code_is_zero_when_no_date_failed():
    assert qas_backfill.exit_code({"failed": {}}) == 0
