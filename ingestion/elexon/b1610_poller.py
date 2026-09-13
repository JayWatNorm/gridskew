"""Fetch, validate, quarantine and load Elexon B1610 generation volumes."""

import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import psycopg2
import requests
import simplejson
from dotenv import load_dotenv
from psycopg2.extras import execute_values

from ingestion.elexon.contracts import B1610_SPEC
from ingestion.routing import process_rows


def decimal_json_dumps(values):
    """Encode B1610 quarantine payloads without converting Decimal values."""

    return simplejson.dumps(values, use_decimal=True)


def fetch(from_date, to_date):
    """Fetch B1610 rows while preserving source decimal precision."""

    response = requests.get(
        "https://data.elexon.co.uk/bmrs/api/v1/datasets/B1610/stream",
        params={
            "from": from_date.strftime("%Y-%m-%dT%H:%MZ"),
            "to": to_date.strftime("%Y-%m-%dT%H:%MZ"),
        },
        headers={
            "User-Agent": "gridskew/0.1 (+https://github.com/JayWatNorm/gridskew)"
        },
        timeout=60,
    )
    response.raise_for_status()
    return json.loads(response.text, parse_float=Decimal)


def run(conn, from_date=None, to_date=None):
    """Validate and load one B1610 window at the expected II publication lag."""

    retrieved_at = datetime.now(timezone.utc)
    day_start = retrieved_at.replace(hour=0, minute=0, second=0, microsecond=0)
    if to_date is None or from_date is None:
        from_date = day_start - timedelta(days=15)
        to_date = day_start - timedelta(days=14)

    rows = fetch(from_date, to_date)
    if not isinstance(rows, list) or not rows:
        raise RuntimeError(
            "Invalid response returned from Elexon B1610 API: expected a non-empty list"
        )

    rejected_count = process_rows(
        rows,
        spec=B1610_SPEC,
        dataset="B1610",
        conn=conn,
        retrieved_at=retrieved_at,
        request_context={
            "from": from_date.isoformat(),
            "to": to_date.isoformat(),
        },
        parse_rows=parse,
        load_rows=load,
        payload_dumps=decimal_json_dumps,
    )
    if rejected_count:
        raise RuntimeError(
            f"Quarantined {rejected_count} rows due to validation errors"
        )


def parse(source_rows, retrieved_at):
    """Convert compatible source dictionaries to typed B1610 insert tuples."""

    parsed_rows = []
    for source_row in source_rows:
        parsed_rows.append(
            (
                source_row["bmUnit"],
                source_row["nationalGridBmUnitId"],
                source_row["psrType"],
                date.fromisoformat(source_row["settlementDate"]),
                source_row["settlementPeriod"],
                datetime.strptime(
                    source_row["halfHourEndTime"], "%Y-%m-%dT%H:%M:%S"
                ).replace(tzinfo=timezone.utc),
                source_row["settlementRunType"],
                source_row["quantity"],
                retrieved_at,
            )
        )
    return parsed_rows


def load(parsed_rows, conn):
    """Insert typed B1610 rows, ignoring existing target keys, and commit."""

    insert_sql = (
        "INSERT INTO raw.elexon_b1610 (bm_unit, national_grid_bm_unit_id, psr_type, "
        "settlement_date, settlement_period, half_hour_end_time, settlement_run_type, "
        "quantity, retrieved_at) VALUES %s ON CONFLICT (bm_unit, settlement_date, "
        "settlement_period, settlement_run_type) DO NOTHING"
    )

    with conn.cursor() as cursor:
        execute_values(cursor, insert_sql, parsed_rows, page_size=1000)
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
