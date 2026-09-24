"""Archive complete Elexon BM-unit registry responses."""

import logging
import os
import re
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from uuid import uuid4

import psycopg2
import requests
from dotenv import load_dotenv
from psycopg2.extras import execute_values

from ingestion.elexon.contracts import BM_UNITS_SPEC
from ingestion.routing import quarantine_rows
from ingestion.validation import validate_rows

URL = "https://data.elexon.co.uk/bmrs/api/v1/reference/bmunits/all"
USER_AGENT = "gridskew/0.1 (+https://github.com/JayWatNorm/gridskew)"
CAPACITY_FIELDS = ("demandCapacity", "generationCapacity")
CAPACITY_WHITESPACE = " \t\n\r\f\v"
CAPACITY_NUMBER = re.compile(
    r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z"
)
RAW_FIELDS = (
    "nationalGridBmUnit",
    "elexonBmUnit",
    "eic",
    "fuelType",
    "leadPartyName",
    "bmUnitType",
    "fpnFlag",
    "bmUnitName",
    "leadPartyId",
    "demandCapacity",
    "generationCapacity",
    "productionOrConsumptionFlag",
    "transmissionLossFactor",
    "workingDayCreditAssessmentImportCapability",
    "nonWorkingDayCreditAssessmentImportCapability",
    "workingDayCreditAssessmentExportCapability",
    "nonWorkingDayCreditAssessmentExportCapability",
    "creditQualifyingStatus",
    "demandInProductionFlag",
    "gspGroupId",
    "gspGroupName",
    "interconnectorId",
)

logger = logging.getLogger(__name__)


def fetch():
    """Fetch the complete current registry in a single request."""

    response = requests.get(URL, headers={"User-Agent": USER_AGENT}, timeout=30)
    response.raise_for_status()
    return response.json()


def _finding(index, row, error):
    return {"index": index, "row": row, "errors": [error], "warnings": []}


def validate_extract(rows):
    """Return rejected findings and the number of distinct BM units."""

    if not isinstance(rows, list) or not rows:
        raise RuntimeError("Invalid BM-unit response: expected a non-empty list")

    findings = validate_rows(rows, BM_UNITS_SPEC)
    rejected = [finding for finding in findings if finding["errors"]]
    by_unit = defaultdict(list)

    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        key = row.get("nationalGridBmUnit")
        if not isinstance(key, str) or not key.strip():
            rejected.append(_finding(index, row, "Blank nationalGridBmUnit"))
            continue
        by_unit[key].append((index, row))

        for field in CAPACITY_FIELDS:
            value = row.get(field)
            if not isinstance(value, str):
                continue
            value = value.strip(CAPACITY_WHITESPACE)
            if not value:
                continue
            try:
                if not CAPACITY_NUMBER.fullmatch(value):
                    raise InvalidOperation
                number = Decimal(value)
                # PostgreSQL unconstrained numeric supports at most 131072
                # digits before the point and 16383 after it.
                if (
                    not number.is_finite()
                    or number.adjusted() >= 131072
                    or number.as_tuple().exponent < -16383
                ):
                    raise InvalidOperation
            except InvalidOperation:
                rejected.append(_finding(index, row, f"Invalid numeric {field}"))

        if findings[index]["warnings"]:
            logger.warning(
                "BM-unit row %s has source contract warnings: %s",
                index,
                findings[index]["warnings"],
            )

    for entries in by_unit.values():
        if len(entries) == 1:
            continue
        seen_eics = set()
        reference = {key: value for key, value in entries[0][1].items() if key != "eic"}
        for index, row in entries:
            eic = row.get("eic")
            if not isinstance(eic, str) or not eic.strip():
                rejected.append(
                    _finding(index, row, "Duplicate unit needs distinct EICs")
                )
                continue
            if eic in seen_eics:
                rejected.append(
                    _finding(index, row, "Duplicate unit needs distinct EICs")
                )
            seen_eics.add(eic)
            if {key: value for key, value in row.items() if key != "eic"} != reference:
                rejected.append(
                    _finding(index, row, "Duplicate unit attributes disagree")
                )

    # A bad row can have several independent reasons; keep one quarantine entry
    # per source position with all of its reasons.
    combined = {}
    for finding in rejected:
        entry = combined.setdefault(
            finding["index"],
            {
                "index": finding["index"],
                "row": finding["row"],
                "errors": [],
                "warnings": [],
            },
        )
        entry["errors"].extend(finding["errors"])
    return list(combined.values()), len(by_unit)


def previous_extract(conn):
    """Read the latest committed manifest and its unit keys."""

    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT extract_id, row_count, unit_count "
            "FROM raw.elexon_bm_units_extracts "
            "ORDER BY retrieved_at DESC, extract_id DESC LIMIT 1"
        )
        previous = cursor.fetchone()
        if previous is None:
            return None, set()
        cursor.execute(
            "SELECT DISTINCT national_grid_bm_unit FROM raw.elexon_bm_units "
            "WHERE extract_id = %s",
            (previous[0],),
        )
        return previous, {row[0] for row in cursor.fetchall()}


def check_completeness(rows, unit_count, previous, previous_keys, *, first_min=2500):
    """Block obvious truncation or mass disappearance before publication."""

    if previous is None:
        if len(rows) < first_min:
            raise RuntimeError("BM-unit first extract is below the reviewed minimum")
        return

    _, previous_rows, previous_units = previous
    if len(rows) < previous_rows * 0.95 or unit_count < previous_units * 0.95:
        raise RuntimeError("BM-unit extract shrank by more than five percent")

    new_keys = {row["nationalGridBmUnit"] for row in rows}
    if len(previous_keys - new_keys) > previous_units * 0.05:
        raise RuntimeError("BM-unit extract lost more than five percent of unit keys")


def load_extract(conn, rows, retrieved_at, unit_count, *, extract_id=None):
    """Commit all source rows and their success manifest as one transaction."""

    extract_id = extract_id or uuid4()
    values = [
        (str(extract_id), index, *(row[field] for field in RAW_FIELDS))
        for index, row in enumerate(rows)
    ]
    columns = "extract_id, source_index, " + ", ".join(
        (
            "national_grid_bm_unit",
            "elexon_bm_unit",
            "eic",
            "fuel_type",
            "lead_party_name",
            "bm_unit_type",
            "fpn_flag",
            "bm_unit_name",
            "lead_party_id",
            "demand_capacity",
            "generation_capacity",
            "production_or_consumption_flag",
            "transmission_loss_factor",
            "working_day_credit_assessment_import_capability",
            "non_working_day_credit_assessment_import_capability",
            "working_day_credit_assessment_export_capability",
            "non_working_day_credit_assessment_export_capability",
            "credit_qualifying_status",
            "demand_in_production_flag",
            "gsp_group_id",
            "gsp_group_name",
            "interconnector_id",
        )
    )
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO raw.elexon_bm_units_extracts "
                "(extract_id, retrieved_at, row_count, unit_count) "
                "VALUES (%s, %s, %s, %s)",
                (str(extract_id), retrieved_at, len(rows), unit_count),
            )
            execute_values(
                cursor,
                f"INSERT INTO raw.elexon_bm_units ({columns}) VALUES %s",
                values,
                page_size=1000,
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return extract_id


@contextmanager
def locked_extract(conn, expected_id):
    """Keep a verified current extract stable while dbt reads it.

    SHARE locks block inserts, updates and deletes from every database writer,
    while allowing dbt's SELECTs from its separate connections. The caller must
    use a dedicated connection and close it after this context exits.
    """

    try:
        with conn.cursor() as cursor:
            cursor.execute("SET LOCAL lock_timeout = '30s'")
            cursor.execute("SET LOCAL idle_in_transaction_session_timeout = 0")
            cursor.execute(
                "LOCK TABLE raw.elexon_bm_units_extracts, raw.elexon_bm_units "
                "IN SHARE MODE"
            )
            cursor.execute(
                "SELECT extract_id, row_count, unit_count "
                "FROM raw.elexon_bm_units_extracts "
                "ORDER BY retrieved_at DESC, extract_id DESC LIMIT 1"
            )
            manifest = cursor.fetchone()
            if manifest is None or str(manifest[0]) != str(expected_id):
                raise RuntimeError("The expected BM-unit extract is not latest")
            cursor.execute(
                "SELECT count(*), count(DISTINCT national_grid_bm_unit) "
                "FROM raw.elexon_bm_units WHERE extract_id = %s",
                (str(expected_id),),
            )
            if cursor.fetchone() != (manifest[1], manifest[2]):
                raise RuntimeError("The BM-unit manifest counts do not match raw rows")
        yield
    finally:
        conn.rollback()


def run(conn):
    """Validate, archive and publish one complete current response."""

    retrieved_at = datetime.now(timezone.utc)
    rows = fetch()
    rejected, unit_count = validate_extract(rows)
    if rejected:
        quarantine_rows(
            rejected,
            conn,
            "BM_UNITS",
            retrieved_at,
            {"endpoint": "/reference/bmunits/all"},
        )
        raise RuntimeError(f"Rejected {len(rejected)} BM-unit source rows")

    previous, previous_keys = previous_extract(conn)
    try:
        check_completeness(rows, unit_count, previous, previous_keys)
    except Exception:
        conn.rollback()
        raise
    return load_extract(conn, rows, retrieved_at, unit_count)


if __name__ == "__main__":
    load_dotenv()
    logging.basicConfig(level=logging.INFO)
    connection = psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT"),
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )
    try:
        print(run(connection))
    finally:
        connection.close()
