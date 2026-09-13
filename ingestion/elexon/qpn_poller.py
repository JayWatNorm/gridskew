"""Fetch, validate, quarantine and load Elexon Quiescent Physical Notifications."""

import logging
import os
from datetime import date, datetime, timedelta, timezone

import psycopg2
import requests
from dotenv import load_dotenv
from psycopg2.extras import execute_values

from ingestion.elexon.contracts import QPN_SPEC
from ingestion.routing import process_rows


def fetch(from_date, to_date):
    """Fetch decoded QPN rows for a UTC datetime window."""

    response = requests.get(
        "https://data.elexon.co.uk/bmrs/api/v1/datasets/QPN/stream",
        params={
            "from": from_date.strftime("%Y-%m-%dT%H:%MZ"),
            "to": to_date.strftime("%Y-%m-%dT%H:%MZ"),
        },
        headers={
            "User-Agent": "gridskew/0.1 (+https://github.com/JayWatNorm/gridskew)"
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def run(conn, from_date=None, to_date=None):
    """Validate and load one QPN window, defaulting to the previous UTC day."""

    retrieved_at = datetime.now(timezone.utc)
    day_start = retrieved_at.replace(hour=0, minute=0, second=0, microsecond=0)
    if to_date is None or from_date is None:
        from_date = day_start - timedelta(days=1)
        to_date = day_start

    rows = fetch(from_date, to_date)
    if not isinstance(rows, list) or not rows:
        raise RuntimeError(
            "Invalid response returned from Elexon QPN API: expected a non-empty list"
        )

    rejected_count = process_rows(
        rows,
        spec=QPN_SPEC,
        dataset="QPN",
        conn=conn,
        retrieved_at=retrieved_at,
        request_context={
            "from": from_date.isoformat(),
            "to": to_date.isoformat(),
        },
        parse_rows=parse,
        load_rows=load,
    )
    if rejected_count:
        raise RuntimeError(
            f"Quarantined {rejected_count} rows due to validation errors"
        )


def parse(source_rows, retrieved_at):
    """Convert compatible source dictionaries to typed QPN insert tuples."""

    parsed_rows = []
    for source_row in source_rows:
        parsed_rows.append(
            (
                date.fromisoformat(source_row["settlementDate"]),
                source_row["settlementPeriod"],
                datetime.strptime(source_row["timeFrom"], "%Y-%m-%dT%H:%M:%SZ").replace(
                    tzinfo=timezone.utc
                ),
                datetime.strptime(source_row["timeTo"], "%Y-%m-%dT%H:%M:%SZ").replace(
                    tzinfo=timezone.utc
                ),
                source_row["levelFrom"],
                source_row["levelTo"],
                source_row["nationalGridBmUnit"],
                source_row["bmUnit"],
                retrieved_at,
            )
        )
    return parsed_rows


def load(parsed_rows, conn):
    """Insert typed QPN rows, ignoring existing target keys, and commit."""

    insert_sql = (
        "INSERT INTO raw.elexon_qpn (settlement_date, settlement_period, time_from, "
        "time_to, level_from, level_to, national_grid_bm_unit, bm_unit, retrieved_at) "
        "VALUES %s ON CONFLICT (national_grid_bm_unit, time_from, retrieved_at) "
        "DO NOTHING"
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
