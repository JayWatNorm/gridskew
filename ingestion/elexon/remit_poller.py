"""Fetch, validate, quarantine and load Elexon REMIT messages."""

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import psycopg2
import requests
import simplejson
from dotenv import load_dotenv
from psycopg2.extras import Json, execute_values

from ingestion.elexon.contracts import REMIT_SPEC
from ingestion.routing import process_rows

# One year before the first Physical Notification day, so that a notice
# published up to a year ahead of its outage is held.
HISTORY_START = datetime(2024, 8, 22, tzinfo=timezone.utc)
OVERLAP = timedelta(hours=1)
WINDOW = timedelta(days=1)
SECONDS_BETWEEN_REQUESTS = 0.2


def decimal_json_dumps(values):
    """Encode REMIT payloads without converting Decimal values."""

    return simplejson.dumps(values, use_decimal=True)


def fetch(publish_from, publish_to):
    """Fetch the REMIT messages published in a window, both ends included."""

    response = requests.get(
        "https://data.elexon.co.uk/bmrs/api/v1/datasets/REMIT/stream",
        params={
            "publishDateTimeFrom": _utc_text_to_the_minute(publish_from),
            "publishDateTimeTo": _utc_text_to_the_minute(publish_to),
        },
        headers={
            "User-Agent": "gridskew/0.1 (+https://github.com/JayWatNorm/gridskew)"
        },
        timeout=60,
    )
    response.raise_for_status()
    return json.loads(response.text, parse_float=Decimal)


def _utc_text_to_the_minute(value):
    # The newest stored publish time comes back in the database session's
    # time zone, which need not be UTC.
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def run(conn, first_publish_time=None):
    """Load every message published since the newest stored one.

    The window comes from the table, so a run after missed runs fetches the
    whole gap. On an empty table it starts at HISTORY_START. Pass
    first_publish_time to load an earlier range again.

    The run fails while any REMIT row is in quarantine, after it has loaded
    the compatible rows.
    """

    retrieved_at = datetime.now(timezone.utc)
    if first_publish_time is None:
        newest_stored = newest_stored_publish_time(conn)
        if newest_stored is None:
            first_publish_time = HISTORY_START
        else:
            first_publish_time = newest_stored - OVERLAP

    is_first_request = True
    for window_start, window_end in build_windows(first_publish_time, retrieved_at):
        if not is_first_request:
            time.sleep(SECONDS_BETWEEN_REQUESTS)
        is_first_request = False

        rows = fetch(window_start, window_end)
        if not isinstance(rows, list):
            raise RuntimeError(
                "Invalid response returned from Elexon REMIT API: expected a list"
            )
        # A window with no message is normal: nothing was published in it.
        if not rows:
            continue

        process_rows(
            rows,
            spec=REMIT_SPEC,
            dataset="REMIT",
            conn=conn,
            retrieved_at=retrieved_at,
            request_context={
                "publishDateTimeFrom": window_start.isoformat(),
                "publishDateTimeTo": window_end.isoformat(),
            },
            parse_rows=parse,
            load_rows=load,
            payload_dumps=decimal_json_dumps,
            log_missing_optional_fields=False,
        )

    # Counted from the table, not from this run: a later run starts after
    # the rejected message and would otherwise succeed without it.
    quarantined_count = quarantined_row_count(conn)
    if quarantined_count:
        raise RuntimeError(
            f"{quarantined_count} REMIT rows are in quarantine; see "
            "docs/sources/elexon/031_remit_ingestion.md"
        )


def newest_stored_publish_time(conn):
    """Return the newest stored publish time, or None when there is none.

    A message stored before its own publish time was dated in the future. It
    never counts: it must not move the window past the messages published
    before it, now or after its date has passed.
    """

    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT max(publish_time) FROM raw.elexon_remit "
            "WHERE publish_time <= retrieved_at"
        )
        newest_publish_time = cursor.fetchone()[0]
    return newest_publish_time


def quarantined_row_count(conn):
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM raw.endpoint_quarantine WHERE dataset = 'REMIT'"
        )
        row_count = cursor.fetchone()[0]
    return row_count


def build_windows(range_start, range_end):
    """Split a publish-time range into consecutive windows of at most a day."""

    windows = []
    window_start = range_start
    while window_start < range_end:
        window_end = min(window_start + WINDOW, range_end)
        windows.append((window_start, window_end))
        window_start = window_end
    return windows


def parse(source_rows, retrieved_at):
    """Convert compatible source dictionaries to typed REMIT insert tuples."""

    parsed_rows = []
    for source_row in source_rows:
        parsed_rows.append(_parse_row(source_row, retrieved_at))
    return parsed_rows


def _parse_row(source_row, retrieved_at):
    mrid = source_row["mrid"]
    revision_number = source_row["revisionNumber"]
    publish_time = _utc_from_text_with_seconds(source_row["publishTime"])
    created_time = _utc_from_text_with_seconds(source_row["createdTime"])
    message_type = source_row.get("messageType")
    event_type = source_row.get("eventType")
    unavailability_type = source_row.get("unavailabilityType")
    event_status = source_row.get("eventStatus")
    asset_id = source_row.get("assetId")
    affected_unit = source_row.get("affectedUnit")
    fuel_type = source_row.get("fuelType")
    normal_capacity = source_row.get("normalCapacity")
    available_capacity = source_row.get("availableCapacity")
    unavailable_capacity = source_row.get("unavailableCapacity")
    event_start_time = _utc_from_text_with_seconds(source_row["eventStartTime"])
    event_end_time = _utc_from_text_with_seconds(source_row.get("eventEndTime"))
    payload = Json(source_row, dumps=decimal_json_dumps)

    return (
        mrid,
        revision_number,
        publish_time,
        created_time,
        message_type,
        event_type,
        unavailability_type,
        event_status,
        asset_id,
        affected_unit,
        fuel_type,
        normal_capacity,
        available_capacity,
        unavailable_capacity,
        event_start_time,
        event_end_time,
        payload,
        retrieved_at,
    )


def _utc_from_text_with_seconds(value):
    if value is None:
        return None
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def load(parsed_rows, conn):
    """Insert typed REMIT rows, ignoring publications already stored, and commit."""

    insert_sql = (
        "INSERT INTO raw.elexon_remit (mrid, revision_number, publish_time, "
        "created_time, message_type, event_type, unavailability_type, "
        "event_status, asset_id, affected_unit, fuel_type, normal_capacity, "
        "available_capacity, unavailable_capacity, event_start_time, "
        "event_end_time, payload, retrieved_at) VALUES %s ON CONFLICT "
        "(mrid, revision_number, publish_time, created_time) DO NOTHING"
    )

    with conn.cursor() as cursor:
        execute_values(cursor, insert_sql, parsed_rows, page_size=1000)
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
