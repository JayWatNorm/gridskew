"""Run the outturn history gap query against a disposable PostgreSQL.

The unit tests replace the query with a mock. This loads controlled rows, asks
which history windows hold missing periods, and rolls everything back, so the
database is left as it was found.

Run from the repository root with the dbt environment set, for example:
    GRIDSKEW_DISPOSABLE_TEST=1 DBT_HOST=localhost DBT_DBNAME=gridskew_dev ...
    python -m tests.adhoc.check_outturn_gap_refetch
"""

import os
from datetime import datetime, timedelta, timezone

import psycopg2

from ingestion.carbon_intensity.outturn_poller import (
    floor_to_period,
    gap_windows,
    missing_period_starts,
)

DAY = timedelta(days=1)
HALF_HOUR = timedelta(minutes=30)
RUN_TIME = datetime(2026, 10, 12, 6, 0, 17, tzinfo=timezone.utc)
REQUEST_END = RUN_TIME - DAY
HORIZON_START = REQUEST_END - timedelta(days=365)
RECENT_START = REQUEST_END - timedelta(days=7)
HISTORY_START = datetime(2025, 8, 19, 6, 0, tzinfo=timezone.utc)
FIRST_CAPTURE = datetime(2026, 8, 19, 6, 0, tzinfo=timezone.utc)
SECOND_CAPTURE = datetime(2026, 8, 20, 6, 0, tzinfo=timezone.utc)

# An interrupted initial load left this hole in old history: 12 periods.
OLD_GAP_START = datetime(2026, 2, 1, 0, 0, tzinfo=timezone.utc)
OLD_GAP_END = datetime(2026, 2, 1, 6, 0, tzinfo=timezone.utc)
# A paused DAG left this hole just behind the seven-day look-back: 96 periods.
RECENT_GAP_START = datetime(2026, 9, 30, 6, 0, tzinfo=timezone.utc)
RECENT_GAP_END = datetime(2026, 10, 2, 6, 0, tzinfo=timezone.utc)

INSERT_EVERY_HALF_HOUR = (
    "INSERT INTO raw.carbon_intensity_outturn (period_start, period_end, "
    "retrieved_at, actual, forecast_final, intensity_index) "
    "SELECT period_start, period_start + interval '30 minutes', "
    "%s, 100, 100, 'moderate' "
    "FROM generate_series(%s::timestamptz, "
    "%s::timestamptz - interval '30 minutes', interval '30 minutes') "
    "AS period_start"
)


def disposable_connection():
    return psycopg2.connect(
        host=os.environ["DBT_HOST"],
        port=os.environ["DBT_PORT"],
        user=os.environ["DBT_USER"],
        password=os.environ["DBT_PASSWORD"],
        dbname=os.environ["DBT_DBNAME"],
    )


def execute(conn, query, params=()):
    with conn.cursor() as cursor:
        cursor.execute(query, params)


def delete_periods(conn, gap_start, gap_end):
    execute(
        conn,
        "DELETE FROM raw.carbon_intensity_outturn "
        "WHERE period_start >= %s AND period_start < %s",
        (gap_start, gap_end),
    )


def holds(windows, period_start):
    for window_start, window_end in windows:
        if window_start <= period_start < window_end:
            return True
    return False


def check_gap_windows(conn):
    execute(conn, "DELETE FROM raw.carbon_intensity_outturn")
    # Two captures of every period: a stored period has several rows.
    for retrieved_at in (FIRST_CAPTURE, SECOND_CAPTURE):
        execute(conn, INSERT_EVERY_HALF_HOUR, (retrieved_at, HISTORY_START, RUN_TIME))

    complete = gap_windows(conn, HORIZON_START, RECENT_START, REQUEST_END)
    assert complete == [], f"complete history reported gaps: {complete}"

    delete_periods(conn, OLD_GAP_START, OLD_GAP_END)
    delete_periods(conn, RECENT_GAP_START, RECENT_GAP_END)

    missing = missing_period_starts(
        conn,
        floor_to_period(HORIZON_START) + DAY,
        floor_to_period(RECENT_START) - HALF_HOUR,
    )
    assert len(missing) == 12 + 96, f"expected 108 missing periods, got {len(missing)}"
    assert missing[0] == OLD_GAP_START
    assert missing[-1] == RECENT_GAP_END - HALF_HOUR

    windows = gap_windows(conn, HORIZON_START, RECENT_START, REQUEST_END)
    assert len(windows) == 2, f"expected two windows with gaps, got {windows}"
    assert holds(windows, OLD_GAP_START), "the old gap's window is missing"
    assert holds(windows, RECENT_GAP_START), "the recent gap's window is missing"
    print("Outturn gap re-fetch: missing periods and their windows are found")


def main():
    if (
        os.getenv("GRIDSKEW_DISPOSABLE_TEST") != "1"
        or os.getenv("DBT_HOST") not in {"localhost", "127.0.0.1"}
        or os.getenv("DBT_DBNAME") != "gridskew_dev"
    ):
        raise RuntimeError(
            "Outturn gap checks require opt-in to a local disposable gridskew_dev"
        )
    conn = disposable_connection()
    try:
        check_gap_windows(conn)
    finally:
        conn.rollback()
        conn.close()


if __name__ == "__main__":
    main()
