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


def fetch(from_date, to_date):
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


def run(conn):
    """Backfill one year when needed; otherwise refresh the last seven days."""

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
    if last_period_start is None or first_period_start > horizon_start:
        windows = build_windows(horizon_start, request_end)
    else:
        windows = [(recent_start, request_end)]

    rejected_count = 0
    incomplete_count = 0
    for window_start, window_end in windows:
        window_rejected_count, incomplete = process_window(
            conn, window_start, window_end, retrieved_at
        )
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


def process_window(conn, from_date, to_date, retrieved_at):
    """Validate and load one outturn request window."""

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
    parsed_rows = []
    for source_row in source_rows:
        parsed_rows.append(
            (
                datetime.strptime(source_row["from"], "%Y-%m-%dT%H:%MZ").replace(
                    tzinfo=timezone.utc
                ),
                datetime.strptime(source_row["to"], "%Y-%m-%dT%H:%MZ").replace(
                    tzinfo=timezone.utc
                ),
                retrieved_at,
                source_row["intensity"]["actual"],
                source_row["intensity"]["forecast"],
                source_row["intensity"]["index"],
            )
        )
    return parsed_rows


def load(parsed_rows, conn):
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
