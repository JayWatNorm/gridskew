import logging
import os
from datetime import datetime, timezone

import psycopg2
import requests
from dotenv import load_dotenv
from psycopg2.extras import execute_values

from ingestion.carbon_intensity.completeness import validate_forecast_window
from ingestion.carbon_intensity.contracts import FORECAST_SPEC
from ingestion.routing import process_rows

logger = logging.getLogger(__name__)


def fetch(timestamp):
    response = requests.get(
        f"https://api.carbonintensity.org.uk/intensity/{timestamp}/fw48h",
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
            "Invalid response returned from Carbon Intensity forecast API: "
            "expected a non-empty data list"
        )

    rows = response.get("data")
    if not isinstance(rows, list) or not rows:
        raise RuntimeError(
            "Invalid response returned from Carbon Intensity forecast API: "
            "expected a non-empty data list"
        )
    return rows


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
                source_row["intensity"]["forecast"],
                source_row["intensity"]["actual"],
                source_row["intensity"]["index"],
            )
        )
    return parsed_rows


def load(parsed_rows, conn):
    insert_sql = (
        "INSERT INTO raw.carbon_intensity_forecast (period_start, "
        "period_end, retrieved_at, forecast, actual, intensity_index) VALUES %s"
    )

    with conn.cursor() as cursor:
        execute_values(cursor, insert_sql, parsed_rows)
    conn.commit()


def run(conn):
    """Validate and load one 48-hour forecast observation."""

    retrieved_at = datetime.now(timezone.utc)
    timestamp = retrieved_at.strftime("%Y-%m-%dT%H:%MZ")
    rows = response_rows(fetch(timestamp))
    request_context = {"timestamp": timestamp, "horizon_hours": 48}

    rejected_count = process_rows(
        rows,
        spec=FORECAST_SPEC,
        dataset="CI_Forecast",
        conn=conn,
        retrieved_at=retrieved_at,
        request_context=request_context,
        parse_rows=parse,
        load_rows=load,
    )
    if rejected_count:
        raise RuntimeError(
            f"Quarantined {rejected_count} rows due to validation errors"
        )
    validate_forecast_window(rows, retrieved_at)
    logger.info("Retrieved %s rows at %s", len(rows), retrieved_at.isoformat())


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
