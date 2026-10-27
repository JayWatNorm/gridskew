"""Fetch, validate, quarantine and load one Carbon Intensity 48-hour forecast."""

import logging
from datetime import datetime, timezone

import requests

from ingestion.carbon_intensity.completeness import (
    forecast_covers_48_hours,
    validate_forecast_periods,
)
from ingestion.carbon_intensity.contracts import FORECAST_SPEC
from ingestion.database import insert_rows
from ingestion.routing import ContractRejected, process_rows
from ingestion.source_api import (
    USER_AGENT,
    carbon_intensity_rows,
    utc_from_text_to_the_minute,
)

logger = logging.getLogger(__name__)


def fetch(timestamp):
    """Fetch the 48-hour forecast as seen at `timestamp` (text, UTC)."""

    response = requests.get(
        f"https://api.carbonintensity.org.uk/intensity/{timestamp}/fw48h",
        headers={"User-Agent": USER_AGENT},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def parse(source_rows, retrieved_at):
    """Convert compatible source dictionaries to typed forecast insert tuples."""

    parsed_rows = []
    for source_row in source_rows:
        parsed_rows.append(_parse_row(source_row, retrieved_at))
    return parsed_rows


def _parse_row(source_row, retrieved_at):
    period_start = utc_from_text_to_the_minute(source_row["from"])
    period_end = utc_from_text_to_the_minute(source_row["to"])
    intensity = source_row["intensity"]
    forecast = intensity["forecast"]
    actual = intensity["actual"]
    intensity_index = intensity["index"]

    return (period_start, period_end, retrieved_at, forecast, actual, intensity_index)


def load(parsed_rows, conn):
    """Insert typed forecast rows and commit. Every capture is kept."""

    insert_sql = (
        "INSERT INTO raw.carbon_intensity_forecast (period_start, "
        "period_end, retrieved_at, forecast, actual, intensity_index) VALUES %s"
    )

    with conn.cursor() as cursor:
        insert_rows(cursor, insert_sql, parsed_rows)
    conn.commit()


def run(conn):
    """Validate and load one 48-hour forecast observation.

    A window shorter than 48 hours is stored and logged as a warning, and the
    run succeeds: the source feed has stopped extending, and a retry would
    only store the same rows again.
    """

    retrieved_at = datetime.now(timezone.utc)
    timestamp = retrieved_at.strftime("%Y-%m-%dT%H:%MZ")
    rows = carbon_intensity_rows(fetch(timestamp), "forecast")
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
        raise ContractRejected(
            f"Quarantined {rejected_count} rows due to validation errors"
        )
    validate_forecast_periods(rows, retrieved_at)
    if not forecast_covers_48_hours(rows):
        logger.warning(
            "Carbon Intensity forecast window is shorter than 48 hours: "
            "stored %s rows at %s; the source feed has stopped extending",
            len(rows),
            retrieved_at.isoformat(),
        )
        return
    logger.info("Retrieved %s rows at %s", len(rows), retrieved_at.isoformat())
