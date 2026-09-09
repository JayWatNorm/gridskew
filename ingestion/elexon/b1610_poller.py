import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import psycopg2
import requests
import simplejson
from dotenv import load_dotenv
from psycopg2.extras import Json, execute_values

from ingestion.elexon.contracts import B1610_SPEC
from ingestion.validation import run as validate_rows

logger = logging.getLogger(__name__)


def decimal_json_dumps(values):
    return simplejson.dumps(values, use_decimal=True)


def fetch(from_date, to_date):
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
    """Load one B1610 window, defaulting to the current II publication lag."""

    retrieved_at = datetime.now(timezone.utc)
    retrieved_at_day_start = retrieved_at.replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    if to_date is None or from_date is None:
        from_date = retrieved_at_day_start - timedelta(days=15)
        to_date = retrieved_at_day_start - timedelta(days=14)
    request_context = {
        "from": from_date.isoformat(),
        "to": to_date.isoformat(),
    }
    result = fetch(from_date, to_date)
    if (not result) or (not isinstance(result, list)):
        raise RuntimeError(
            "Invalid response returned from Elexon B1610 API: expected a non-empty list"
        )
    validation_findings = validate_rows(result, spec=B1610_SPEC)
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
            "dataset": "B1610",
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
    if valid_rows:
        par_result = parse(valid_rows, retrieved_at)
        load(par_result, conn)
    if error_rows:
        raise RuntimeError(
            f"Quarantined {len(error_rows)} rows due to validation errors"
        )
    return


def parse(results, retrieved_at):
    rows = [
        (
            result["bmUnit"],
            result["nationalGridBmUnitId"],
            result["psrType"],
            date.fromisoformat(result["settlementDate"]),
            result["settlementPeriod"],
            datetime.strptime(result["halfHourEndTime"], "%Y-%m-%dT%H:%M:%S").replace(
                tzinfo=timezone.utc
            ),
            result["settlementRunType"],
            result["quantity"],
            retrieved_at,
        )
        for result in results
    ]
    return rows


def quarantine_rows(rows, conn, retrieved_at, request_context):
    """Insert rejected B1610 findings into the endpoint quarantine and commit."""

    quarantined_at = datetime.now(timezone.utc)

    insert_sql = (
        "INSERT INTO raw.endpoint_quarantine (dataset, retrieved_at, request_context,"
        "validation_errors, observed_fields, payload, quarantined_at) VALUES %s"
    )

    insert_values = [
        (
            "B1610",
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
            Json(finding["row"], dumps=decimal_json_dumps),
            quarantined_at,
        )
        for finding in rows
    ]

    with conn.cursor() as cursor:
        execute_values(cursor, insert_sql, insert_values, page_size=1000)
    conn.commit()


def load(results, conn):
    insert_sql = (
        "INSERT INTO raw.elexon_b1610 (bm_unit, national_grid_bm_unit_id, psr_type, "
        "settlement_date, settlement_period, half_hour_end_time, settlement_run_type,"
        "quantity, retrieved_at) VALUES %s ON CONFLICT (bm_unit,"
        "settlement_date, settlement_period, settlement_run_type) DO NOTHING"
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
