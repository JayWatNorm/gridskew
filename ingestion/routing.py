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

    rejected_findings = []
    compatible_findings = []
    for finding in validate_rows(rows, spec=spec):
        if finding["errors"]:
            rejected_findings.append(finding)
        else:
            compatible_findings.append(finding)

    _log_warnings(compatible_findings, dataset, retrieved_at, request_context)

    if rejected_findings:
        quarantine_rows(
            rejected_findings,
            conn,
            dataset,
            retrieved_at,
            request_context,
            payload_dumps=payload_dumps,
        )

    if compatible_findings:
        compatible_rows = []
        for finding in compatible_findings:
            compatible_rows.append(finding["row"])
        parsed_rows = parse_rows(compatible_rows, retrieved_at)
        load_rows(parsed_rows, conn)

    return len(rejected_findings)


def _log_warnings(findings, dataset, retrieved_at, request_context):
    """Log each distinct warning once, with how many rows it affects."""

    source_indexes_by_warning = {}
    for finding in findings:
        for warning in finding["warnings"]:
            if warning not in source_indexes_by_warning:
                source_indexes_by_warning[warning] = []
            source_indexes_by_warning[warning].append(finding["index"])

    for warning, source_indexes in source_indexes_by_warning.items():
        reason, field = warning.split(": ", 1)
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
    rejected_findings = rows
    insert_values = []
    for finding in rejected_findings:
        source_row = finding["row"]
        validation_errors = {
            "source_index": finding["index"],
            "errors": finding["errors"],
        }
        observed_fields = _observed_fields(source_row)
        payload = _json_value(source_row, payload_dumps)
        insert_values.append(
            (
                dataset,
                retrieved_at,
                Json(request_context),
                Json(validation_errors),
                Json(observed_fields),
                payload,
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
