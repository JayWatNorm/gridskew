"""Fetch, validate, quarantine and load Elexon PN and QPN rows.

Physical Notifications (PN) and Quiescent Physical Notifications (QPN) share
one row shape and one stream endpoint. `pn_poller` and `qpn_poller` name the
dataset and its table; this module does the work for both.
"""

import json
import logging
from datetime import date, datetime, timezone

import requests

from ingestion.database import insert_rows
from ingestion.routing import ContractRejected, process_rows
from ingestion.source_api import USER_AGENT, utc_from_text_with_seconds

logger = logging.getLogger(__name__)


def fetch(dataset, from_date, to_date):
    """Fetch decoded rows of one dataset ("PN" or "QPN") for a UTC window."""

    response = requests.get(
        f"https://data.elexon.co.uk/bmrs/api/v1/datasets/{dataset}/stream",
        params={
            "from": from_date.strftime("%Y-%m-%dT%H:%MZ"),
            "to": to_date.strftime("%Y-%m-%dT%H:%MZ"),
        },
        headers={"User-Agent": USER_AGENT},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def run(conn, from_date, to_date, *, dataset, spec, load_rows):
    """Validate one window of a dataset and load it with that dataset's loader."""

    retrieved_at = datetime.now(timezone.utc)

    rows = fetch(dataset, from_date, to_date)
    if not isinstance(rows, list) or not rows:
        raise RuntimeError(
            f"Invalid response returned from Elexon {dataset} API: "
            "expected a non-empty list"
        )

    rejected_count = process_rows(
        rows,
        spec=spec,
        dataset=dataset,
        conn=conn,
        retrieved_at=retrieved_at,
        request_context={"from": from_date.isoformat(), "to": to_date.isoformat()},
        parse_rows=parse,
        load_rows=load_rows,
    )
    if rejected_count:
        raise ContractRejected(
            f"Quarantined {rejected_count} rows due to validation errors"
        )


def parse(source_rows, retrieved_at):
    """Convert compatible source dictionaries to typed insert tuples."""

    parsed_rows = []
    for source_row in source_rows:
        parsed_rows.append(_parse_row(source_row, retrieved_at))
    return parsed_rows


def _parse_row(source_row, retrieved_at):
    settlement_date = date.fromisoformat(source_row["settlementDate"])
    settlement_period = source_row["settlementPeriod"]
    time_from = utc_from_text_with_seconds(source_row["timeFrom"])
    time_to = utc_from_text_with_seconds(source_row["timeTo"])
    level_from = source_row["levelFrom"]
    level_to = source_row["levelTo"]
    national_grid_bm_unit = source_row["nationalGridBmUnit"]
    bm_unit = source_row["bmUnit"]

    return (
        settlement_date,
        settlement_period,
        time_from,
        time_to,
        level_from,
        level_to,
        national_grid_bm_unit,
        bm_unit,
        retrieved_at,
    )


def load(parsed_rows, conn, table, dataset):
    """Insert typed rows into `table`, ignoring existing target keys, and commit.

    `table` comes from `pn_poller` or `qpn_poller`, never from source data.
    Every row of one load shares its retrieved_at, so a row skipped on the key
    is a second segment of the same unit starting at the same instant in this
    capture. Only the first is stored, and a warning gives the count.
    """

    insert_sql = (
        f"INSERT INTO {table} (settlement_date, settlement_period, time_from, "
        "time_to, level_from, level_to, national_grid_bm_unit, bm_unit, retrieved_at) "
        "VALUES %s ON CONFLICT (national_grid_bm_unit, time_from, retrieved_at) "
        "DO NOTHING"
    )

    with conn.cursor() as cursor:
        insert_rows(cursor, insert_sql, parsed_rows)
        inserted_count = cursor.rowcount
    conn.commit()

    skipped_count = len(parsed_rows) - inserted_count
    if skipped_count:
        payload = {
            "dataset": dataset,
            "severity": "warning",
            "reason": "Rows share a unit and start time within one capture",
            "rows_sent": len(parsed_rows),
            "rows_inserted": inserted_count,
            "rows_skipped": skipped_count,
        }
        logger.warning("%s", json.dumps(payload))
