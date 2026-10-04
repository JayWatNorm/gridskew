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
SOURCE_FIELDS_AND_COLUMNS = (
    ("nationalGridBmUnit", "national_grid_bm_unit"),
    ("elexonBmUnit", "elexon_bm_unit"),
    ("eic", "eic"),
    ("fuelType", "fuel_type"),
    ("leadPartyName", "lead_party_name"),
    ("bmUnitType", "bm_unit_type"),
    ("fpnFlag", "fpn_flag"),
    ("bmUnitName", "bm_unit_name"),
    ("leadPartyId", "lead_party_id"),
    ("demandCapacity", "demand_capacity"),
    ("generationCapacity", "generation_capacity"),
    ("productionOrConsumptionFlag", "production_or_consumption_flag"),
    ("transmissionLossFactor", "transmission_loss_factor"),
    (
        "workingDayCreditAssessmentImportCapability",
        "working_day_credit_assessment_import_capability",
    ),
    (
        "nonWorkingDayCreditAssessmentImportCapability",
        "non_working_day_credit_assessment_import_capability",
    ),
    (
        "workingDayCreditAssessmentExportCapability",
        "working_day_credit_assessment_export_capability",
    ),
    (
        "nonWorkingDayCreditAssessmentExportCapability",
        "non_working_day_credit_assessment_export_capability",
    ),
    ("creditQualifyingStatus", "credit_qualifying_status"),
    ("demandInProductionFlag", "demand_in_production_flag"),
    ("gspGroupId", "gsp_group_id"),
    ("gspGroupName", "gsp_group_name"),
    ("interconnectorId", "interconnector_id"),
)

logger = logging.getLogger(__name__)


def fetch():
    """Fetch the complete current registry in a single request."""

    response = requests.get(URL, headers={"User-Agent": USER_AGENT}, timeout=30)
    response.raise_for_status()
    return response.json()


def validate_extract(rows):
    """Return rejected findings and the number of distinct BM units."""

    if not isinstance(rows, list) or not rows:
        raise RuntimeError("Invalid BM-unit response: expected a non-empty list")

    contract_findings = validate_rows(rows, BM_UNITS_SPEC)
    rejected = _findings_with_errors(contract_findings)

    rows_by_unit = defaultdict(list)
    for source_index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue

        unit = row.get("nationalGridBmUnit")
        if not _is_usable_identifier(unit):
            rejected.append(_rejection(source_index, row, "Blank nationalGridBmUnit"))
            continue
        rows_by_unit[unit].append((source_index, row))

        for field in CAPACITY_FIELDS:
            if _has_invalid_capacity_text(row.get(field)):
                rejected.append(
                    _rejection(source_index, row, f"Invalid numeric {field}")
                )

        contract_warnings = contract_findings[source_index]["warnings"]
        if contract_warnings:
            logger.warning(
                "BM-unit row %s has source contract warnings: %s",
                source_index,
                contract_warnings,
            )

    for positioned_rows in rows_by_unit.values():
        rejected.extend(_duplicate_unit_rejections(positioned_rows))

    return _merged_by_source_index(rejected), len(rows_by_unit)


def _findings_with_errors(findings):
    with_errors = []
    for finding in findings:
        if finding["errors"]:
            with_errors.append(finding)
    return with_errors


def _rejection(source_index, row, error):
    return {"index": source_index, "row": row, "errors": [error], "warnings": []}


def _is_usable_identifier(value):
    return isinstance(value, str) and bool(value.strip())


def _has_invalid_capacity_text(value):
    """Only text is judged here; the field contract rejects other types.

    Blank text means the capacity is unknown and is accepted.
    """

    if not isinstance(value, str):
        return False
    text = value.strip(CAPACITY_WHITESPACE)
    if not text:
        return False
    if not CAPACITY_NUMBER.fullmatch(text):
        return True
    try:
        number = Decimal(text)
    except InvalidOperation:
        return True
    if not number.is_finite():
        return True
    # PostgreSQL unconstrained numeric supports at most 131072 digits before
    # the point and 16383 after it.
    if number.adjusted() >= 131072:
        return True
    if number.as_tuple().exponent < -16383:
        return True
    return False


def _duplicate_unit_rejections(positioned_rows):
    """One unit may repeat only with a distinct EIC per row and all else equal."""

    if len(positioned_rows) == 1:
        return []

    rejected = []
    seen_eics = set()
    _, first_row = positioned_rows[0]
    expected_attributes = _without_eic(first_row)
    for source_index, row in positioned_rows:
        eic = row.get("eic")
        if not _is_usable_identifier(eic):
            rejected.append(
                _rejection(source_index, row, "Duplicate unit needs distinct EICs")
            )
            continue
        if eic in seen_eics:
            rejected.append(
                _rejection(source_index, row, "Duplicate unit needs distinct EICs")
            )
        seen_eics.add(eic)
        if _without_eic(row) != expected_attributes:
            rejected.append(
                _rejection(source_index, row, "Duplicate unit attributes disagree")
            )
    return rejected


def _without_eic(row):
    attributes = {}
    for field, value in row.items():
        if field != "eic":
            attributes[field] = value
    return attributes


def _merged_by_source_index(rejections):
    """A row can be rejected for several reasons; keep one entry with all of them."""

    merged = {}
    for rejection in rejections:
        source_index = rejection["index"]
        if source_index not in merged:
            merged[source_index] = {
                "index": source_index,
                "row": rejection["row"],
                "errors": [],
                "warnings": [],
            }
        merged[source_index]["errors"].extend(rejection["errors"])
    return list(merged.values())


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

    source_fields = []
    column_names = ["extract_id", "source_index"]
    for source_field, column_name in SOURCE_FIELDS_AND_COLUMNS:
        source_fields.append(source_field)
        column_names.append(column_name)
    columns = ", ".join(column_names)

    values = []
    for source_index, row in enumerate(rows):
        source_values = [row[source_field] for source_field in source_fields]
        values.append((str(extract_id), source_index, *source_values))

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
