"""Fetch, validate, quarantine and load Elexon B1610 generation volumes."""

import json
import logging
import os
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import psycopg2
import requests
import simplejson
from dotenv import load_dotenv
from psycopg2.extras import execute_values

from ingestion.elexon.contracts import B1610_SPEC
from ingestion.routing import process_rows

logger = logging.getLogger(__name__)

LONDON = ZoneInfo("Europe/London")
HALF_HOUR = timedelta(minutes=30)


def decimal_json_dumps(values):
    """Encode B1610 quarantine payloads without converting Decimal values."""

    return simplejson.dumps(values, use_decimal=True)


def fetch(from_date, to_date, settlement_run_type=None):
    """Fetch B1610 rows while preserving source decimal precision."""

    params = {
        "from": from_date.strftime("%Y-%m-%dT%H:%MZ"),
        "to": to_date.strftime("%Y-%m-%dT%H:%MZ"),
    }
    if settlement_run_type is not None:
        params["settlementRunType"] = settlement_run_type
    response = requests.get(
        "https://data.elexon.co.uk/bmrs/api/v1/datasets/B1610/stream",
        params=params,
        headers={
            "User-Agent": "gridskew/0.1 (+https://github.com/JayWatNorm/gridskew)"
        },
        timeout=60,
    )
    response.raise_for_status()
    return json.loads(response.text, parse_float=Decimal)


def run(
    conn,
    from_date=None,
    to_date=None,
    settlement_run_type=None,
    *,
    expected_settlement_date=None,
):
    """Validate and load one B1610 window at the expected II publication lag."""

    retrieved_at = datetime.now(timezone.utc)
    day_start = retrieved_at.replace(hour=0, minute=0, second=0, microsecond=0)
    if to_date is None or from_date is None:
        from_date = day_start - timedelta(days=15)
        to_date = day_start - timedelta(days=14)

    rows = fetch(from_date, to_date, settlement_run_type)
    if not isinstance(rows, list) or not rows:
        raise RuntimeError(
            "Invalid response returned from Elexon B1610 API: expected a non-empty list"
        )

    if expected_settlement_date is not None:
        validate_cohort_response(rows, expected_settlement_date)

    request_context = {
        "from": from_date.isoformat(),
        "to": to_date.isoformat(),
    }
    if settlement_run_type is not None:
        request_context["settlementRunType"] = settlement_run_type

    rejected_count = process_rows(
        rows,
        spec=B1610_SPEC,
        dataset="B1610",
        conn=conn,
        retrieved_at=retrieved_at,
        request_context=request_context,
        parse_rows=parse,
        load_rows=load,
        payload_dumps=decimal_json_dumps,
    )
    if rejected_count:
        raise RuntimeError(
            f"Quarantined {rejected_count} rows due to validation errors"
        )

    return rows


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


def london_midnight_utc(settlement_date):
    """Return the UTC instant at which a British settlement date starts."""

    local_midnight = datetime(
        settlement_date.year,
        settlement_date.month,
        settlement_date.day,
        tzinfo=LONDON,
    )
    return local_midnight.astimezone(timezone.utc)


def expected_period_count(settlement_date):
    """Return 46, 48 or 50 settlement periods for a British date."""

    start = london_midnight_utc(settlement_date)
    end = london_midnight_utc(settlement_date + timedelta(days=1))
    return int((end - start) / HALF_HOUR)


def british_day_window(settlement_date):
    """Return inclusive period-start API bounds for one British date.

    B1610 filters on period starts, despite returning halfHourEndTime.
    Live one-unit requests verified this boundary behaviour on 1 Oct 2026.
    """

    start = london_midnight_utc(settlement_date)
    end = london_midnight_utc(settlement_date + timedelta(days=1))
    return start, end - HALF_HOUR


def validate_cohort_response(rows, settlement_date):
    """Refuse out-of-date or out-of-range cohort rows before any writes.

    Envelope and field-type errors still use the existing quarantine route.
    A date/period mismatch rejects the batch; valid but incomplete batches
    still commit before the stored coverage check.
    """

    expected_date = settlement_date.isoformat()
    expected = expected_period_count(settlement_date)
    for row in rows:
        if not isinstance(row, dict):
            continue
        returned_date = row.get("settlementDate")
        if isinstance(returned_date, str) and returned_date != expected_date:
            raise RuntimeError(
                f"Invalid cohort response: returned settlement date {returned_date!r}; "
                f"expected {expected_date}"
            )
        period = row.get("settlementPeriod")
        if type(period) is int and not 1 <= period <= expected:
            raise RuntimeError(
                f"Invalid cohort response: returned settlement period {period}; "
                f"expected 1..{expected} for {expected_date}"
            )


def cohort_coverage(conn, settlement_date, run_type):
    """Return stored rows, periods and units for one date and settlement run."""

    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT count(*), array_agg(DISTINCT settlement_period), "
            "count(DISTINCT bm_unit) FROM raw.elexon_b1610 "
            "WHERE settlement_date = %s AND settlement_run_type = %s",
            (settlement_date, run_type),
        )
        return cursor.fetchone()


def capture_cohort_date(conn, settlement_date, run_type):
    """Load one settlement date for one run, then fail if it is incomplete."""

    from_date, to_date = british_day_window(settlement_date)
    rows = run(
        conn,
        from_date,
        to_date,
        settlement_run_type=run_type,
        expected_settlement_date=settlement_date,
    )
    returned = Counter(
        row.get("settlementRunType") for row in rows if isinstance(row, dict)
    )
    row_count, stored_periods, unit_count = cohort_coverage(
        conn, settlement_date, run_type
    )
    conn.rollback()
    expected = expected_period_count(settlement_date)
    stored_periods = set(stored_periods or [])
    period_count = len(stored_periods)
    expected_periods = set(range(1, expected + 1))
    summary = {
        "settlement_date": settlement_date.isoformat(),
        "run_type": run_type,
        "returned_run_types": dict(returned),
        "stored_rows": row_count,
        "stored_periods": period_count,
        "stored_period_numbers": sorted(stored_periods),
        "expected_periods": expected,
        "stored_units": unit_count,
    }
    logger.info("%s", json.dumps(summary))

    problems = []
    if set(returned) != {run_type}:
        problems.append(f"returned run types {dict(returned)}")
    if period_count != expected:
        problems.append(f"{period_count} of {expected} periods stored")
    if stored_periods != expected_periods:
        problems.append(
            "stored period numbers: "
            f"missing {sorted(expected_periods - stored_periods)}, "
            f"unexpected {sorted(stored_periods - expected_periods)}"
        )
    if problems:
        raise RuntimeError(
            f"Incomplete {run_type} capture for {settlement_date}: "
            + "; ".join(problems)
        )
    return summary


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
