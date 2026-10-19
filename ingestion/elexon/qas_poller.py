"""Fetch, validate, quarantine and load Elexon balancing services volumes (QAS).

QAS is the ABSVD that NESO sends for each BM unit and settlement period.
One capture is one British settlement date: one request, or two on a
50-period date.
"""

import json
import logging
from collections import Counter
from datetime import date, datetime, timezone
from decimal import Decimal

import requests

from ingestion.database import insert_rows
from ingestion.elexon.b1610_poller import decimal_json_dumps, expected_period_count
from ingestion.elexon.contracts import QAS_SPEC
from ingestion.routing import ContractRejected, process_rows

logger = logging.getLogger(__name__)

QAS_URL = "https://data.elexon.co.uk/bmrs/api/v1/balancing/nonbm/volumes"
# The most periods a British date can have; a shorter date returns fewer.
LAST_PERIOD_ASKED = 50
# The last period of the first of the two requests on a 50-period date.
FIRST_HALF_LAST_PERIOD = 25


def fetch(settlement_date):
    """Fetch one settlement date's QAS rows, keeping source decimal precision.

    With the period parameters, Elexon reads from and to as settlement dates;
    without them it reads start times, and a summer date loses periods 1 and 2.
    Elexon refuses a request whose period start times span more than a day, so
    a 50-period date is asked for in two requests and their rows combined.
    """

    bodies = []
    for first_period, last_period in requested_period_ranges(settlement_date):
        bodies.append(fetch_periods(settlement_date, first_period, last_period))
    if len(bodies) == 1:
        return bodies[0]

    rows = []
    for body in bodies:
        if not isinstance(body, dict) or not isinstance(body.get("data"), list):
            return body
        rows.extend(body["data"])
    return {"data": rows}


def requested_period_ranges(settlement_date):
    """The period ranges to ask for: one request, or two on a 50-period date."""

    if expected_period_count(settlement_date) < LAST_PERIOD_ASKED:
        return ((1, LAST_PERIOD_ASKED),)
    return (
        (1, FIRST_HALF_LAST_PERIOD),
        (FIRST_HALF_LAST_PERIOD + 1, LAST_PERIOD_ASKED),
    )


def fetch_periods(settlement_date, first_period, last_period):
    response = requests.get(
        QAS_URL,
        params={
            "from": settlement_date.isoformat(),
            "to": settlement_date.isoformat(),
            "settlementPeriodFrom": first_period,
            "settlementPeriodTo": last_period,
            "format": "json",
        },
        headers={
            "User-Agent": "gridskew/0.1 (+https://github.com/JayWatNorm/gridskew)"
        },
        timeout=60,
    )
    response.raise_for_status()
    return json.loads(response.text, parse_float=Decimal)


def run(conn, settlement_date):
    """Capture one settlement date; return a summary of what was loaded.

    Every scheduled date is past NESO's publication window, so an empty
    response is a failed capture, retried like any other failure. A response
    that fails a check, or holds a row the contract rejects, loads nothing.
    """

    retrieved_at = datetime.now(timezone.utc)

    body = fetch(settlement_date)
    rows = None
    if isinstance(body, dict):
        rows = body.get("data")
    if not isinstance(rows, list) or not rows:
        raise RuntimeError(
            "Invalid response returned from Elexon QAS API: expected a non-empty list"
        )

    check_response(rows, settlement_date)

    rejected_count = process_rows(
        rows,
        spec=QAS_SPEC,
        dataset="QAS",
        conn=conn,
        retrieved_at=retrieved_at,
        request_context={"settlementDate": settlement_date.isoformat()},
        parse_rows=parse,
        load_rows=load,
        payload_dumps=decimal_json_dumps,
        load_all_or_none=True,
    )
    if rejected_count:
        raise ContractRejected(
            f"Quarantined {rejected_count} rows due to validation errors"
        )

    summary = capture_summary(rows, settlement_date)
    logger.info("%s", json.dumps(summary))
    return summary


def check_response(rows, settlement_date):
    """Refuse a response that is not one whole settlement date, before any write.

    Every row must carry the requested date, a period of that date and a unit
    and period no other row has, and every period must have a row. Field types
    are left to the contract.
    """

    expected_date = settlement_date.isoformat()
    period_count = expected_period_count(settlement_date)
    seen_unit_periods = set()
    seen_periods = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        returned_date = row.get("settlementDate")
        if isinstance(returned_date, str) and returned_date != expected_date:
            raise RuntimeError(
                f"Invalid response: returned settlement date {returned_date!r}; "
                f"expected {expected_date}"
            )
        period = row.get("settlementPeriod")
        if type(period) is not int:
            continue
        if not 1 <= period <= period_count:
            raise RuntimeError(
                f"Invalid response: returned settlement period {period}; "
                f"expected 1..{period_count} for {expected_date}"
            )
        seen_periods.add(period)
        unit = row.get("nationalGridBmUnit")
        if not isinstance(unit, str):
            continue
        if (unit, period) in seen_unit_periods:
            raise RuntimeError(
                f"Invalid response: two rows for {unit} period {period} "
                f"on {expected_date}"
            )
        seen_unit_periods.add((unit, period))

    missing_periods = sorted(set(range(1, period_count + 1)) - seen_periods)
    if missing_periods:
        raise RuntimeError(
            f"Invalid response: no rows for periods {missing_periods} "
            f"on {expected_date}"
        )


def capture_summary(rows, settlement_date):
    """Rows, units and periods loaded, and the fewest rows in any period."""

    rows_per_period = Counter()
    units = set()
    for row in rows:
        rows_per_period[row["settlementPeriod"]] += 1
        units.add(row["nationalGridBmUnit"])
    return {
        "settlement_date": settlement_date.isoformat(),
        "rows": len(rows),
        "units": len(units),
        "periods": len(rows_per_period),
        "fewest_rows_in_a_period": min(rows_per_period.values()),
    }


def parse(source_rows, retrieved_at):
    """Convert compatible source dictionaries to typed QAS insert tuples."""

    parsed_rows = []
    for source_row in source_rows:
        parsed_rows.append(
            (
                date.fromisoformat(source_row["settlementDate"]),
                source_row["settlementPeriod"],
                source_row["nationalGridBmUnit"],
                source_row["bmUnit"],
                source_row["bmUnitApplicableBalancingServicesVolume"],
                retrieved_at,
            )
        )
    return parsed_rows


def load(parsed_rows, conn):
    """Insert typed QAS rows and commit.

    No ON CONFLICT: a capture has its own retrieved_at, and check_response has
    already refused a repeated unit and period.
    """

    insert_sql = (
        "INSERT INTO raw.elexon_qas (settlement_date, settlement_period, "
        "national_grid_bm_unit, bm_unit, "
        "bm_unit_applicable_balancing_services_volume, retrieved_at) VALUES %s"
    )

    with conn.cursor() as cursor:
        insert_rows(cursor, insert_sql, parsed_rows)
    conn.commit()
