"""Fetch, validate, quarantine and load Elexon demand forecast and outturn.

NDF is the national demand forecast, reissued every half-hour; INDO is the
matching outturn. One request is one publish-time window of one dataset.
Every issue is kept: publish_time is part of each table's key, so fetching a
window again inserts nothing.
"""

import json
import logging
from datetime import date, datetime, timezone

import requests

from ingestion.database import insert_rows
from ingestion.elexon.contracts import INDO_SPEC, NDF_SPEC
from ingestion.routing import ContractRejected, process_rows
from ingestion.source_api import USER_AGENT, utc_from_text_with_seconds

logger = logging.getLogger(__name__)

BASE_URL = "https://data.elexon.co.uk/bmrs/api/v1/datasets"


def fetch(dataset, publish_from, publish_to):
    """Fetch the rows of one dataset published in a window."""

    response = requests.get(
        f"{BASE_URL}/{dataset}",
        params={
            "publishDateTimeFrom": _utc_text_to_the_minute(publish_from),
            "publishDateTimeTo": _utc_text_to_the_minute(publish_to),
        },
        headers={"User-Agent": USER_AGENT},
        timeout=60,
    )
    response.raise_for_status()
    return response.json()


def _utc_text_to_the_minute(value):
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def run(conn, dataset, publish_from, publish_to):
    """Load one publish window of NDF or INDO; return a summary.

    A window with no issue is valid and loads nothing. A row the contract
    rejects is quarantined and then no row of the window is loaded, so a
    window is stored whole or not at all.
    """

    spec, parse, load = DATASETS[dataset]
    retrieved_at = datetime.now(timezone.utc)

    body = fetch(dataset, publish_from, publish_to)
    rows = None
    if isinstance(body, dict):
        rows = body.get("data")
    if not isinstance(rows, list):
        raise RuntimeError(
            f"Invalid response returned from Elexon {dataset} API: expected a data list"
        )

    if rows:
        rejected_count = process_rows(
            rows,
            spec=spec,
            dataset=dataset,
            conn=conn,
            retrieved_at=retrieved_at,
            request_context={
                "publishDateTimeFrom": publish_from.isoformat(),
                "publishDateTimeTo": publish_to.isoformat(),
            },
            parse_rows=parse,
            load_rows=load,
            load_all_or_none=True,
        )
        if rejected_count:
            raise ContractRejected(
                f"Quarantined {rejected_count} rows due to validation errors"
            )

    summary = {
        "dataset": dataset,
        "publish_from": publish_from.isoformat(),
        "publish_to": publish_to.isoformat(),
        "rows": len(rows),
        "issues": len(publish_times(rows)),
    }
    logger.info("%s", json.dumps(summary))
    return summary


def publish_times(rows):
    """The distinct publish times in a response: one per issue."""

    issued = set()
    for row in rows:
        issued.add(row["publishTime"])
    return issued


def parse_ndf(source_rows, retrieved_at):
    """Convert compatible source dictionaries to typed NDF insert tuples."""

    parsed_rows = []
    for source_row in source_rows:
        parsed_rows.append(
            (
                date.fromisoformat(source_row["settlementDate"]),
                source_row["settlementPeriod"],
                utc_from_text_with_seconds(source_row["startTime"]),
                source_row["boundary"],
                utc_from_text_with_seconds(source_row["publishTime"]),
                source_row["demand"],
                retrieved_at,
            )
        )
    return parsed_rows


def parse_indo(source_rows, retrieved_at):
    """Convert compatible source dictionaries to typed INDO insert tuples."""

    parsed_rows = []
    for source_row in source_rows:
        parsed_rows.append(
            (
                date.fromisoformat(source_row["settlementDate"]),
                source_row["settlementPeriod"],
                utc_from_text_with_seconds(source_row["startTime"]),
                utc_from_text_with_seconds(source_row["publishTime"]),
                source_row["demand"],
                retrieved_at,
            )
        )
    return parsed_rows


def load_ndf(parsed_rows, conn):
    """Insert typed NDF rows, ignoring issues already stored, and commit."""

    insert_sql = (
        "INSERT INTO raw.elexon_ndf (settlement_date, settlement_period, "
        "start_time, boundary, publish_time, demand, retrieved_at) VALUES %s "
        "ON CONFLICT (settlement_date, settlement_period, boundary, publish_time) "
        "DO NOTHING"
    )

    with conn.cursor() as cursor:
        insert_rows(cursor, insert_sql, parsed_rows)
    conn.commit()


def load_indo(parsed_rows, conn):
    """Insert typed INDO rows, ignoring issues already stored, and commit."""

    insert_sql = (
        "INSERT INTO raw.elexon_indo (settlement_date, settlement_period, "
        "start_time, publish_time, demand, retrieved_at) VALUES %s "
        "ON CONFLICT (settlement_date, settlement_period, publish_time) "
        "DO NOTHING"
    )

    with conn.cursor() as cursor:
        insert_rows(cursor, insert_sql, parsed_rows)
    conn.commit()


DATASETS = {
    "NDF": (NDF_SPEC, parse_ndf, load_ndf),
    "INDO": (INDO_SPEC, parse_indo, load_indo),
}
