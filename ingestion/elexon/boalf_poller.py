"""Fetch, validate, quarantine and load Elexon bid-offer acceptance levels."""

import logging
import os
from datetime import date, datetime, timedelta, timezone

import psycopg2
import requests
from dotenv import load_dotenv
from psycopg2.extras import execute_values

from ingestion.elexon.contracts import BOALF_SPEC
from ingestion.routing import process_rows


def fetch(from_date, to_date):
    """Fetch decoded BOALF rows whose ramp point starts in a UTC window."""

    response = requests.get(
        "https://data.elexon.co.uk/bmrs/api/v1/datasets/BOALF/stream",
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
    """Validate and load one BOALF window, defaulting to the previous UTC day."""

    retrieved_at = datetime.now(timezone.utc)
    day_start = retrieved_at.replace(hour=0, minute=0, second=0, microsecond=0)
    if from_date is None or to_date is None:
        from_date = day_start - timedelta(days=1)
        to_date = day_start

    rows = fetch(from_date, to_date)
    if not isinstance(rows, list) or not rows:
        raise RuntimeError(
            "Invalid response returned from Elexon BOALF API: expected a non-empty list"
        )

    rejected_count = process_rows(
        rows,
        spec=BOALF_SPEC,
        dataset="BOALF",
        conn=conn,
        retrieved_at=retrieved_at,
        request_context={"from": from_date.isoformat(), "to": to_date.isoformat()},
        parse_rows=parse,
        load_rows=load,
    )
    if rejected_count:
        raise RuntimeError(
            f"Quarantined {rejected_count} rows due to validation errors"
        )


def parse(source_rows, retrieved_at):
    """Convert compatible source dictionaries to typed BOALF insert tuples."""

    parsed_rows = []
    for source_row in source_rows:
        parsed_rows.append(_parse_row(source_row, retrieved_at))
    return parsed_rows


def _parse_row(source_row, retrieved_at):
    national_grid_bm_unit = source_row["nationalGridBmUnit"]
    bm_unit = source_row["bmUnit"]
    acceptance_number = source_row["acceptanceNumber"]
    acceptance_time = _utc_from_text_with_seconds(source_row["acceptanceTime"])
    settlement_date = date.fromisoformat(source_row["settlementDate"])
    settlement_period_from = source_row["settlementPeriodFrom"]
    settlement_period_to = source_row["settlementPeriodTo"]
    time_from = _utc_from_text_with_seconds(source_row["timeFrom"])
    time_to = _utc_from_text_with_seconds(source_row["timeTo"])
    level_from = source_row["levelFrom"]
    level_to = source_row["levelTo"]
    so_flag = source_row["soFlag"]
    deemed_bo_flag = source_row["deemedBoFlag"]
    stor_flag = source_row["storFlag"]
    rr_flag = source_row["rrFlag"]
    amendment_flag = source_row["amendmentFlag"]

    return (
        national_grid_bm_unit,
        bm_unit,
        acceptance_number,
        acceptance_time,
        settlement_date,
        settlement_period_from,
        settlement_period_to,
        time_from,
        time_to,
        level_from,
        level_to,
        so_flag,
        deemed_bo_flag,
        stor_flag,
        rr_flag,
        amendment_flag,
        retrieved_at,
    )


def _utc_from_text_with_seconds(value):
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def load(parsed_rows, conn):
    """Insert typed BOALF rows, ignoring existing target keys, and commit."""

    insert_sql = (
        "INSERT INTO raw.elexon_boalf (national_grid_bm_unit, bm_unit, "
        "acceptance_number, acceptance_time, settlement_date, "
        "settlement_period_from, settlement_period_to, time_from, time_to, "
        "level_from, level_to, so_flag, deemed_bo_flag, stor_flag, rr_flag, "
        "amendment_flag, retrieved_at) VALUES %s ON CONFLICT "
        "(national_grid_bm_unit, acceptance_number, time_from, retrieved_at) "
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
