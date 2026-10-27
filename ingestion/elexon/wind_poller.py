"""Fetch, validate, quarantine and load Elexon wind forecast and outturn.

WINDFOR is the national wind generation forecast: up to eight issues a day,
each forecasting hourly points. FUELHH is the half-hourly outturn by fuel
type; only WIND is asked for. A WINDFOR request is one publish-time window;
a FUELHH request is one settlement date. Every issue and every publication is
kept, so fetching a window or a date again inserts nothing.
"""

import json
import logging
from datetime import date, datetime, timezone

import requests

from ingestion.database import insert_rows
from ingestion.elexon.contracts import FUELHH_SPEC, WINDFOR_SPEC
from ingestion.routing import ContractRejected, process_rows
from ingestion.source_api import (
    USER_AGENT,
    utc_from_text_with_seconds,
    utc_text_to_the_minute,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://data.elexon.co.uk/bmrs/api/v1/datasets"
WIND_FUEL_TYPE = "WIND"


def fetch_windfor(publish_from, publish_to):
    """Fetch every WINDFOR issue published in a window."""

    response = requests.get(
        f"{BASE_URL}/WINDFOR",
        params={
            "publishDateTimeFrom": utc_text_to_the_minute(publish_from),
            "publishDateTimeTo": utc_text_to_the_minute(publish_to),
        },
        headers={"User-Agent": USER_AGENT},
        timeout=60,
    )
    response.raise_for_status()
    return response.json()


def fetch_fuelhh(settlement_date):
    """Fetch the FUELHH wind outturn of one settlement date."""

    response = requests.get(
        f"{BASE_URL}/FUELHH",
        params={
            "settlementDateFrom": settlement_date.isoformat(),
            "settlementDateTo": settlement_date.isoformat(),
            "fuelType": WIND_FUEL_TYPE,
        },
        headers={"User-Agent": USER_AGENT},
        timeout=60,
    )
    response.raise_for_status()
    return response.json()


def run_windfor(conn, publish_from, publish_to):
    """Load one publish window of WINDFOR; return a summary.

    A window with no issue is valid and loads nothing. A row the contract
    rejects is quarantined and then no row of the window is loaded.
    """

    retrieved_at = datetime.now(timezone.utc)
    body = fetch_windfor(publish_from, publish_to)
    request_context = {
        "publishDateTimeFrom": publish_from.isoformat(),
        "publishDateTimeTo": publish_to.isoformat(),
    }
    rows = load_response(conn, "WINDFOR", body, retrieved_at, request_context)

    summary = {
        "dataset": "WINDFOR",
        "publish_from": publish_from.isoformat(),
        "publish_to": publish_to.isoformat(),
        "rows": len(rows),
        "issues": len(distinct_values(rows, "publishTime")),
    }
    logger.info("%s", json.dumps(summary))
    return summary


def run_fuelhh(conn, settlement_date):
    """Load the FUELHH wind outturn of one settlement date; return a summary.

    A date with no row is valid and loads nothing. A row the contract rejects
    is quarantined and then no row of the date is loaded.
    """

    retrieved_at = datetime.now(timezone.utc)
    body = fetch_fuelhh(settlement_date)
    request_context = {
        "settlementDate": settlement_date.isoformat(),
        "fuelType": WIND_FUEL_TYPE,
    }
    rows = load_response(conn, "FUELHH", body, retrieved_at, request_context)

    summary = {
        "dataset": "FUELHH",
        "settlement_date": settlement_date.isoformat(),
        "rows": len(rows),
        "periods": len(distinct_values(rows, "settlementPeriod")),
    }
    logger.info("%s", json.dumps(summary))
    return summary


def load_response(conn, dataset, body, retrieved_at, request_context):
    """Check the envelope, then validate, quarantine and load its rows whole.

    Returns the rows.
    """

    spec, parse, load = DATASETS[dataset]
    rows = None
    if isinstance(body, dict):
        rows = body.get("data")
    if not isinstance(rows, list):
        raise RuntimeError(
            f"Invalid response returned from Elexon {dataset} API: expected a data list"
        )
    if not rows:
        return rows

    rejected_count = process_rows(
        rows,
        spec=spec,
        dataset=dataset,
        conn=conn,
        retrieved_at=retrieved_at,
        request_context=request_context,
        parse_rows=parse,
        load_rows=load,
        load_all_or_none=True,
    )
    if rejected_count:
        raise ContractRejected(
            f"Quarantined {rejected_count} rows due to validation errors"
        )
    return rows


def distinct_values(rows, field):
    values = set()
    for row in rows:
        values.add(row[field])
    return values


def parse_windfor(source_rows, retrieved_at):
    """Convert compatible source dictionaries to typed WINDFOR insert tuples."""

    parsed_rows = []
    for source_row in source_rows:
        parsed_rows.append(
            (
                utc_from_text_with_seconds(source_row["startTime"]),
                utc_from_text_with_seconds(source_row["publishTime"]),
                source_row["generation"],
                retrieved_at,
            )
        )
    return parsed_rows


def parse_fuelhh(source_rows, retrieved_at):
    """Convert compatible source dictionaries to typed FUELHH insert tuples."""

    parsed_rows = []
    for source_row in source_rows:
        parsed_rows.append(
            (
                date.fromisoformat(source_row["settlementDate"]),
                source_row["settlementPeriod"],
                utc_from_text_with_seconds(source_row["startTime"]),
                source_row["fuelType"],
                utc_from_text_with_seconds(source_row["publishTime"]),
                source_row["generation"],
                retrieved_at,
            )
        )
    return parsed_rows


def load_windfor(parsed_rows, conn):
    """Insert typed WINDFOR rows, ignoring issues already stored, and commit."""

    insert_sql = (
        "INSERT INTO raw.elexon_windfor (start_time, publish_time, generation, "
        "retrieved_at) VALUES %s "
        "ON CONFLICT (start_time, publish_time) DO NOTHING"
    )

    with conn.cursor() as cursor:
        insert_rows(cursor, insert_sql, parsed_rows)
    conn.commit()


def load_fuelhh(parsed_rows, conn):
    """Insert typed FUELHH rows, ignoring publications already stored, and commit."""

    insert_sql = (
        "INSERT INTO raw.elexon_fuelhh (settlement_date, settlement_period, "
        "start_time, fuel_type, publish_time, generation, retrieved_at) VALUES %s "
        "ON CONFLICT (settlement_date, settlement_period, fuel_type, publish_time) "
        "DO NOTHING"
    )

    with conn.cursor() as cursor:
        insert_rows(cursor, insert_sql, parsed_rows)
    conn.commit()


DATASETS = {
    "WINDFOR": (WINDFOR_SPEC, parse_windfor, load_windfor),
    "FUELHH": (FUELHH_SPEC, parse_fuelhh, load_fuelhh),
}
