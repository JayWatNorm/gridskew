"""Shared validation, warning and quarantine routing for source rows."""

import json
import logging
from datetime import datetime, timezone

from psycopg2.extras import Json, execute_values

from ingestion.validation import validate_rows

logger = logging.getLogger(__name__)


def process_rows(
    rows,
    *,
    spec,
    dataset,
    conn,
    retrieved_at,
    request_context,
    parse_rows,
    load_rows,
    payload_dumps=None,
):
    """Quarantine rejected rows, load compatible rows and return the rejected count."""

    findings = validate_rows(rows, spec=spec)
    rejected_findings = []
    compatible_rows = []
    warning_groups = {}

    for finding in findings:
        if finding["errors"]:
            rejected_findings.append(finding)
            continue

        compatible_rows.append(finding["row"])
        for warning in finding["warnings"]:
            reason, field = warning.split(": ", 1)
            warning_groups.setdefault((reason, field), []).append(finding["index"])

    for (reason, field), source_indexes in warning_groups.items():
        payload = {
            "dataset": dataset,
            "retrieved_at": retrieved_at.isoformat(),
            "request_context": request_context,
            "severity": "warning",
            "reason": reason,
            "field": field,
            "affected_row_count": len(source_indexes),
            "sample_source_indexes": source_indexes[:5],
        }
        logger.warning("%s", json.dumps(payload))

    if rejected_findings:
        quarantine_rows(
            rejected_findings,
            conn,
            dataset,
            retrieved_at,
            request_context,
            payload_dumps=payload_dumps,
        )

    if compatible_rows:
        parsed_rows = parse_rows(compatible_rows, retrieved_at)
        load_rows(parsed_rows, conn)

    return len(rejected_findings)


def quarantine_rows(
    rows,
    conn,
    dataset,
    retrieved_at,
    request_context,
    *,
    payload_dumps=None,
):
    """Insert rejected source rows and their validation evidence."""

    quarantined_at = datetime.now(timezone.utc)
    insert_sql = (
        "INSERT INTO raw.endpoint_quarantine (dataset, retrieved_at, request_context,"
        "validation_errors, observed_fields, payload, quarantined_at) VALUES %s"
    )
    insert_values = []
    for finding in rows:
        error_details = {
            "source_index": finding["index"],
            "errors": finding["errors"],
        }
        insert_values.append(
            (
                dataset,
                retrieved_at,
                Json(request_context),
                Json(error_details),
                Json(_observed_fields(finding["row"])),
                _json_value(finding["row"], payload_dumps),
                quarantined_at,
            )
        )

    with conn.cursor() as cursor:
        execute_values(cursor, insert_sql, insert_values, page_size=1000)
    conn.commit()


def _json_value(value, dumps):
    if dumps is None:
        return Json(value)
    return Json(value, dumps=dumps)


def _observed_fields(row, prefix=""):
    if not isinstance(row, dict):
        return []

    fields = []
    for field, value in row.items():
        field_path = f"{prefix}.{field}" if prefix else field
        fields.append(field_path)
        if isinstance(value, dict):
            fields.extend(_observed_fields(value, field_path))
    return fields
