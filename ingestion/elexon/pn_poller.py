"""Fetch, validate, quarantine and load Elexon Physical Notifications."""

from datetime import date, datetime, timezone

import requests
from psycopg2.extras import execute_values

from ingestion.elexon.contracts import PN_SPEC
from ingestion.routing import ContractRejected, process_rows


def fetch(from_date, to_date):
    """Fetch decoded PN rows for a UTC datetime window."""

    response = requests.get(
        "https://data.elexon.co.uk/bmrs/api/v1/datasets/PN/stream",
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


def run(conn, from_date, to_date):
    """Validate and load one PN window."""

    retrieved_at = datetime.now(timezone.utc)

    rows = fetch(from_date, to_date)
    if not isinstance(rows, list) or not rows:
        raise RuntimeError(
            "Invalid response returned from Elexon PN API: expected a non-empty list"
        )

    rejected_count = process_rows(
        rows,
        spec=PN_SPEC,
        dataset="PN",
        conn=conn,
        retrieved_at=retrieved_at,
        request_context={"from": from_date.isoformat(), "to": to_date.isoformat()},
        parse_rows=parse,
        load_rows=load,
    )
    if rejected_count:
        raise ContractRejected(
            f"Quarantined {rejected_count} rows due to validation errors"
        )


def parse(source_rows, retrieved_at):
    """Convert compatible source dictionaries to typed PN insert tuples."""

    parsed_rows = []
    for source_row in source_rows:
        parsed_rows.append(_parse_row(source_row, retrieved_at))
    return parsed_rows


def _parse_row(source_row, retrieved_at):
    settlement_date = date.fromisoformat(source_row["settlementDate"])
    settlement_period = source_row["settlementPeriod"]
    time_from = _utc_from_text_with_seconds(source_row["timeFrom"])
    time_to = _utc_from_text_with_seconds(source_row["timeTo"])
    level_from = source_row["levelFrom"]
    level_to = source_row["levelTo"]
    national_grid_bm_unit = source_row["nationalGridBmUnit"]
    bm_unit = source_row["bmUnit"]

    return (
        settlement_date,
        settlement_period,
        time_from,
        time_to,
        level_from,
        level_to,
        national_grid_bm_unit,
        bm_unit,
        retrieved_at,
    )


def _utc_from_text_with_seconds(value):
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def load(parsed_rows, conn):
    """Insert typed PN rows, ignoring existing target keys, and commit."""

    insert_sql = (
        "INSERT INTO raw.elexon_pn (settlement_date, settlement_period, time_from, time_to, level_from, "
        "level_to, national_grid_bm_unit, bm_unit, retrieved_at) VALUES %s ON CONFLICT "
        "(national_grid_bm_unit, time_from, retrieved_at) DO NOTHING"
    )

    with conn.cursor() as cursor:
        execute_values(cursor, insert_sql, parsed_rows, page_size=1000)
    conn.commit()
