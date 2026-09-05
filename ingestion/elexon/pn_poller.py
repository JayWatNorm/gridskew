"""Fetch, validate, quarantine and load Elexon Physical Notifications."""

import json
import logging
import os
from datetime import date, datetime, timedelta, timezone

import psycopg2
import requests
from dotenv import load_dotenv
from psycopg2.extras import Json, execute_values

from ingestion.elexon.contracts import PN_SPEC
from ingestion.validation import run as validate_rows

logger = logging.getLogger(__name__)


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


def run(conn, from_date=None, to_date=None):
    """Validate one PN window, quarantine rejected rows and load compatible rows.

    If either window boundary is absent, use the previous UTC day. Rows with
    warnings but no errors remain compatible with the typed load.
    """

    retrieved_at = datetime.now(timezone.utc)
    retrieved_at_day_start = retrieved_at.replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    if to_date is None or from_date is None:
        from_date = retrieved_at_day_start - timedelta(days=1)
        to_date = retrieved_at_day_start
    request_context = {
        "from": from_date.isoformat(),
        "to": to_date.isoformat(),
    }
    result = fetch(from_date, to_date)
    validation_findings = validate_rows(result, spec=PN_SPEC)
    error_rows = []
    valid_rows = []
    warning_findings = []
    warning_grouped = {}

    for finding in validation_findings:
        if finding["errors"]:
            error_rows.append(finding)
        else:
            valid_rows.append(finding["row"])
            if finding["warnings"]:
                warning_findings.append(finding)

    for finding in warning_findings:
        for warning in finding["warnings"]:
            reason, field = warning.split(": ", 1)
            key = (reason, field)
            if key not in warning_grouped:
                warning_grouped[key] = {
                    "reason": reason,
                    "field": field,
                    "source_indexes": [finding["index"]],
                }
            else:
                warning_grouped[key]["source_indexes"].append(finding["index"])

    for group in warning_grouped.values():
        payload = {
            "dataset": "PN",
            "retrieved_at": retrieved_at.isoformat(),
            "request_context": request_context,
            "severity": "warning",
            "reason": group["reason"],
            "field": group["field"],
            "affected_row_count": len(group["source_indexes"]),
            "sample_source_indexes": group["source_indexes"][:5],
        }
        logger.warning("%s", json.dumps(payload))
    if error_rows:
        quarantine_rows(error_rows, conn, retrieved_at, request_context)
    par_result = parse(valid_rows, retrieved_at)
    load(par_result, conn)
    return


def parse(results, retrieved_at):
    """Convert compatible source dictionaries to typed PN insert tuples."""

    rows = [
        (
            date.fromisoformat(result["settlementDate"]),
            result["settlementPeriod"],
            datetime.strptime(result["timeFrom"], "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc
            ),
            datetime.strptime(result["timeTo"], "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc
            ),
            result["levelFrom"],
            result["levelTo"],
            result["nationalGridBmUnit"],
            result["bmUnit"],
            retrieved_at,
        )
        for result in results
    ]
    return rows


def quarantine_rows(rows, conn, retrieved_at, request_context):
    """Insert rejected PN findings into the endpoint quarantine and commit."""

    quarantined_at = datetime.now(timezone.utc)

    insert_sql = (
        "INSERT INTO raw.endpoint_quarantine (dataset, retrieved_at, request_context,"
        "validation_errors, observed_fields, payload, quarantined_at) VALUES %s"
    )

    insert_values = [
        (
            "PN",
            retrieved_at,
            Json(request_context),
            Json(
                {
                    "source_index": finding["index"],
                    "errors": finding["errors"],
                }
            ),
            Json(
                list(finding["row"].keys()) if isinstance(finding["row"], dict) else []
            ),
            Json(finding["row"]),
            quarantined_at,
        )
        for finding in rows
    ]

    with conn.cursor() as cursor:
        execute_values(cursor, insert_sql, insert_values, page_size=1000)
    conn.commit()


def load(results, conn):
    """Insert typed PN rows, ignoring existing target keys, and commit."""

    insert_sql = (
        "INSERT INTO raw.elexon_pn (settlement_date, settlement_period, time_from, time_to, level_from, "
        "level_to, national_grid_bm_unit, bm_unit, retrieved_at) VALUES %s ON CONFLICT (national_grid_bm_unit, time_from, retrieved_at) DO NOTHING"
    )

    with conn.cursor() as cursor:
        execute_values(cursor, insert_sql, results, page_size=1000)
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
