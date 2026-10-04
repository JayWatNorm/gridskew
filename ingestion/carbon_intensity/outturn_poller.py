"""Fetch, validate, quarantine and load Carbon Intensity outturn windows."""

import logging
import os
from datetime import datetime, timedelta, timezone

import psycopg2
import requests
from dotenv import load_dotenv
from psycopg2.extras import execute_values

from ingestion.carbon_intensity.completeness import validate_outturn_window
from ingestion.carbon_intensity.contracts import OUTTURN_SPEC
from ingestion.routing import process_rows

logger = logging.getLogger(__name__)

HALF_HOUR = timedelta(minutes=30)
MONDAY = 0
# One fixed day a week bounds the requests for a period the source itself
# lacks, whatever the response to the last attempt stored.
HISTORY_REFETCH_WEEKDAY = MONDAY


def fetch(from_date, to_date):
    """Fetch outturn rows for a UTC datetime window."""

    from_timestamp = from_date.strftime("%Y-%m-%dT%H:%MZ")
    to_timestamp = to_date.strftime("%Y-%m-%dT%H:%MZ")
    response = requests.get(
        f"https://api.carbonintensity.org.uk/intensity/{from_timestamp}/{to_timestamp}",
        headers={
            "User-Agent": "gridskew/0.1 (+https://github.com/JayWatNorm/gridskew)"
        },
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def response_rows(response):
    """Return rows from a valid Carbon Intensity response envelope."""

    if not isinstance(response, dict):
        raise RuntimeError(
            "Invalid response returned from Carbon Intensity outturn API: "
            "expected a non-empty data list"
        )

    rows = response.get("data")
    if not isinstance(rows, list) or not rows:
        raise RuntimeError(
            "Invalid response returned from Carbon Intensity outturn API: "
            "expected a non-empty data list"
        )
    return rows


def build_windows(window_start, window_end, chunk_days=30):
    """Split one request range into windows within the API limit."""

    windows = []
    while window_start < window_end:
        next_end = min(window_start + timedelta(days=chunk_days), window_end)
        windows.append((window_start, next_end))
        window_start = next_end
    return windows


def stored_period_summary(conn):
    """Return the stored row count and period bounds."""

    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT count(*), "
            "min(period_start) AS first_period_start, "
            "max(period_start) AS last_period_start "
            "FROM raw.carbon_intensity_outturn;"
        )
        return cursor.fetchone()


def floor_to_period(value):
    """Round down to the start of the containing half-hour."""

    if value.minute < 30:
        period_minute = 0
    else:
        period_minute = 30
    return value.replace(minute=period_minute, second=0, microsecond=0)


def missing_period_starts(conn, first_start, last_start):
    """Return the expected half-hour starts, inclusive, with no stored row."""

    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT expected.period_start "
            "FROM generate_series(%s::timestamptz, %s::timestamptz, "
            "interval '30 minutes') AS expected(period_start) "
            "LEFT JOIN raw.carbon_intensity_outturn AS stored "
            "ON stored.period_start = expected.period_start "
            "WHERE stored.period_start IS NULL "
            "ORDER BY expected.period_start",
            (first_start, last_start),
        )
        rows = cursor.fetchall()

    missing = []
    for (period_start,) in rows:
        missing.append(period_start)
    return missing


def is_history_refetch_day(retrieved_at):
    return retrieved_at.weekday() == HISTORY_REFETCH_WEEKDAY


def gap_windows(conn, horizon_start, recent_start, request_end):
    """Return the 30-day history windows that hold missing periods."""

    missing = missing_period_starts(
        conn,
        floor_to_period(horizon_start) + timedelta(days=1),
        floor_to_period(recent_start) - HALF_HOUR,
    )
    if not missing:
        return []

    windows_with_gaps = []
    for window_start, window_end in build_windows(horizon_start, request_end):
        missing_count = _count_within(missing, window_start, window_end)
        if missing_count == 0:
            continue

        logger.warning(
            "Re-fetching outturn history from %s to %s: %s periods missing",
            window_start.isoformat(),
            window_end.isoformat(),
            missing_count,
        )
        windows_with_gaps.append((window_start, window_end))
    return windows_with_gaps


def _count_within(period_starts, window_start, window_end):
    count = 0
    for period_start in period_starts:
        if window_start <= period_start < window_end:
            count += 1
    return count


def run(conn):
    """Backfill one year when needed; otherwise refresh the last seven days.

    The refresh runs before any history re-fetch, so a failing history window
    cannot stop late actuals from landing.
    """

    retrieved_at = datetime.now(timezone.utc)
    request_end = retrieved_at - timedelta(days=1)
    horizon_start = request_end - timedelta(days=365)
    recent_start = request_end - timedelta(days=7)
    row_count, first_period_start, last_period_start = stored_period_summary(conn)
    logger.info(
        "Stored outturn data: %s rows from %s to %s",
        row_count,
        first_period_start,
        last_period_start,
    )
    history_windows = []
    if last_period_start is None or first_period_start > horizon_start:
        windows = build_windows(horizon_start, request_end)
    else:
        windows = [(recent_start, request_end)]
        if is_history_refetch_day(retrieved_at):
            history_windows = gap_windows(
                conn, horizon_start, recent_start, request_end
            )

    outcomes = []
    for window_start, window_end in windows:
        outcomes.append(process_window(conn, window_start, window_end, retrieved_at))
    for window_start, window_end in history_windows:
        outcomes.append(
            process_history_window(conn, window_start, window_end, retrieved_at)
        )

    rejected_count = 0
    incomplete_count = 0
    for window_rejected_count, incomplete in outcomes:
        rejected_count += window_rejected_count
        if incomplete:
            incomplete_count += 1

    if rejected_count and incomplete_count:
        raise RuntimeError(
            f"Quarantined {rejected_count} rows due to validation errors and "
            f"found {incomplete_count} incomplete outturn windows"
        )
    if rejected_count:
        raise RuntimeError(
            f"Quarantined {rejected_count} rows due to validation errors"
        )
    if incomplete_count:
        raise RuntimeError(
            f"Found {incomplete_count} incomplete Carbon Intensity outturn windows"
        )


def process_history_window(conn, from_date, to_date, retrieved_at):
    """Process one history window; a failed request counts as incomplete.

    One window that cannot be fetched must not stop the windows after it.
    """

    try:
        return process_window(conn, from_date, to_date, retrieved_at)
    except Exception:
        logger.exception(
            "Outturn history re-fetch failed from %s to %s",
            from_date.isoformat(),
            to_date.isoformat(),
        )
        conn.rollback()
        return 0, True


def process_window(conn, from_date, to_date, retrieved_at):
    """Validate and load one outturn request window.

    Return (rejected row count, whether the window was incomplete).
    """

    request_start = from_date.replace(second=0, microsecond=0)
    request_end = to_date.replace(second=0, microsecond=0)
    rows = response_rows(fetch(request_start, request_end))
    request_context = {
        "from": request_start.isoformat(),
        "to": request_end.isoformat(),
    }
    rejected_count = process_rows(
        rows,
        spec=OUTTURN_SPEC,
        dataset="CI_Outturn",
        conn=conn,
        retrieved_at=retrieved_at,
        request_context=request_context,
        parse_rows=parse,
        load_rows=load,
    )
    if rejected_count:
        return rejected_count, False

    try:
        validate_outturn_window(rows, request_start, request_end)
    except ValueError as error:
        logger.warning(
            "Incomplete outturn window from %s to %s: %s",
            request_start,
            request_end,
            error,
        )
        return 0, True

    logger.info("Retrieved %s rows at %s", len(rows), retrieved_at.isoformat())
    return 0, False


def parse(source_rows, retrieved_at):
    """Convert compatible source dictionaries to typed outturn insert tuples."""

    parsed_rows = []
    for source_row in source_rows:
        parsed_rows.append(_parse_row(source_row, retrieved_at))
    return parsed_rows


def _parse_row(source_row, retrieved_at):
    period_start = _utc_from_text_to_the_minute(source_row["from"])
    period_end = _utc_from_text_to_the_minute(source_row["to"])
    intensity = source_row["intensity"]
    actual = intensity["actual"]
    forecast_final = intensity["forecast"]
    intensity_index = intensity["index"]

    return (
        period_start,
        period_end,
        retrieved_at,
        actual,
        forecast_final,
        intensity_index,
    )


def _utc_from_text_to_the_minute(value):
    return datetime.strptime(value, "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)


def load(parsed_rows, conn):
    """Insert typed outturn rows, ignoring existing target keys, and commit."""

    insert_sql = (
        "INSERT INTO raw.carbon_intensity_outturn (period_start, "
        "period_end, retrieved_at, actual, forecast_final, intensity_index) "
        "VALUES %s ON CONFLICT (period_start, retrieved_at) DO NOTHING"
    )

    with conn.cursor() as cursor:
        execute_values(cursor, insert_sql, parsed_rows)
    conn.commit()


if __name__ == "__main__":
    load_dotenv()
    logging.basicConfig(level=logging.INFO)
    conn = psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT"),
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )

    try:
        run(conn)
    finally:
        conn.close()
