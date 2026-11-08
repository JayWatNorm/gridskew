"""Fetch, validate, quarantine and load Elexon REMIT messages.

The hourly run takes its window from the table. The daily re-read asks again
for each of the previous seven UTC publish days and stores only messages the
hourly run missed, never one later than the hourly run's newest stored
message, so it never moves the hourly window.
"""

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import psycopg
import requests
from psycopg.types.json import Jsonb

from ingestion.database import insert_rows
from ingestion.elexon.contracts import REMIT_SPEC
from ingestion.routing import ContractRejected, process_rows
from ingestion.source_api import (
    USER_AGENT,
    decimal_json_dumps,
    utc_from_text_with_seconds,
    utc_text_to_the_minute,
)

# One year before the first Physical Notification day, so that a notice
# published up to a year ahead of its outage is held.
HISTORY_START = datetime(2024, 8, 22, tzinfo=timezone.utc)
OVERLAP = timedelta(hours=1)
WINDOW = timedelta(days=1)
SECONDS_BETWEEN_REQUESTS = 0.2
REREAD_DAYS = 7
REREAD_PAUSE_SECONDS = 1
TOO_MANY_REQUESTS = 429
DAY = timedelta(days=1)

logger = logging.getLogger(__name__)


def fetch(publish_from, publish_to):
    """Fetch the REMIT messages published in a window, both ends included."""

    response = requests.get(
        "https://data.elexon.co.uk/bmrs/api/v1/datasets/REMIT/stream",
        params={
            "publishDateTimeFrom": utc_text_to_the_minute(publish_from),
            "publishDateTimeTo": utc_text_to_the_minute(publish_to),
        },
        headers={"User-Agent": USER_AGENT},
        timeout=60,
    )
    response.raise_for_status()
    return json.loads(response.text, parse_float=Decimal)


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
        raise ContractRejected(
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
        (newest_publish_time,) = cursor.fetchone()
    return newest_publish_time


def quarantined_row_count(conn):
    """Count quarantined REMIT rows whose message is still not stored.

    A row whose message a later load stored is resolved and no longer
    counts, so no one has to delete it. The match compares text and converts
    nothing from the payload, so a malformed row matches nothing and still
    counts.
    """

    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT count(*)
            FROM raw.endpoint_quarantine AS quarantined
            WHERE quarantined.dataset = 'REMIT'
              AND NOT EXISTS (
                  SELECT 1
                  FROM raw.elexon_remit AS stored
                  WHERE stored.mrid = quarantined.payload ->> 'mrid'
                    AND stored.revision_number::text
                        = quarantined.payload ->> 'revisionNumber'
                    AND to_char(stored.publish_time AT TIME ZONE 'UTC',
                                'YYYY-MM-DD"T"HH24:MI:SS"Z"')
                        = quarantined.payload ->> 'publishTime'
                    AND to_char(stored.created_time AT TIME ZONE 'UTC',
                                'YYYY-MM-DD"T"HH24:MI:SS"Z"')
                        = quarantined.payload ->> 'createdTime'
              )
            """
        )
        (row_count,) = cursor.fetchone()
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
    publish_time = utc_from_text_with_seconds(source_row["publishTime"])
    created_time = utc_from_text_with_seconds(source_row["createdTime"])
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
    event_start_time = utc_from_text_with_seconds(source_row["eventStartTime"])
    event_end_time = _utc_from_optional_text_with_seconds(
        source_row.get("eventEndTime")
    )
    payload = Jsonb(source_row, dumps=decimal_json_dumps)

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


def _utc_from_optional_text_with_seconds(value):
    if value is None:
        return None
    return utc_from_text_with_seconds(value)


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
        insert_rows(cursor, insert_sql, parsed_rows)
    conn.commit()


def reread_days(last_day):
    """The seven UTC publish days up to last_day, oldest first: the oldest
    has no later re-read, so it is asked before a stop can leave it out."""

    days = []
    for days_before in range(REREAD_DAYS - 1, -1, -1):
        days.append(last_day - timedelta(days=days_before))
    return days


def utc_day_start(day):
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc)


def reread_windows(days, watermark):
    """The window to ask for each day, and the days skipped.

    A day starting at or after the watermark is skipped; a day running past
    it ends at the watermark. The watermark is the hourly run's newest
    stored publish time, so nothing asked lies beyond it.
    """

    windows = []
    skipped = []
    for day in days:
        window_start = utc_day_start(day)
        if window_start >= watermark:
            skipped.append(day)
            continue
        day_end = window_start + DAY
        windows.append(
            {
                "day": day,
                "start": window_start,
                "request_end": min(day_end, watermark),
                "day_end": day_end,
            }
        )
    return windows, skipped


def rows_in_window(rows, window_start, day_end, watermark):
    """The rows to store and the count left out.

    A row is left out when its publish time lies outside [window_start,
    day_end) or after the watermark: it belongs to another window, or would
    move the hourly window. A row whose publish time cannot be read is kept,
    so it fails where the hourly run's rows fail.
    """

    kept = []
    left_out = 0
    for row in rows:
        publish_time = readable_publish_time(row)
        if publish_time is None:
            kept.append(row)
            continue
        if publish_time < window_start or publish_time >= day_end:
            left_out += 1
            continue
        if publish_time > watermark:
            left_out += 1
            continue
        kept.append(row)
    return kept, left_out


def readable_publish_time(row):
    publish_text = row.get("publishTime")
    if not isinstance(publish_text, str):
        return None
    try:
        return utc_from_text_with_seconds(publish_text)
    except ValueError:
        return None


def request_time():
    return datetime.now(timezone.utc)


def reread(conn, last_day):
    """Ask again for each of the seven publish days up to last_day; store
    only the messages not stored yet, none later than the hourly watermark.

    Held, with no request, while any REMIT row is in quarantine. Stops at
    once on HTTP 429 or a database error; carries on past any other failed
    day and fails after the last one. Returns the summary.
    """

    summary = new_reread_summary(last_day)
    quarantined_count = quarantined_row_count(conn)
    watermark = newest_stored_publish_time(conn)
    conn.rollback()
    if quarantined_count:
        summary["held"] = True
        logger.info("%s", json.dumps(summary))
        raise ContractRejected(
            f"{quarantined_count} REMIT rows are in quarantine; the re-read waits "
            "for them: see docs/sources/elexon/031_remit_ingestion.md"
        )
    if watermark is None:
        logger.info("%s", json.dumps(summary))
        return summary

    summary["watermark"] = watermark.astimezone(timezone.utc).isoformat()
    windows, skipped = reread_windows(reread_days(last_day), watermark)
    summary["skipped"] = iso_dates(skipped)
    for window in windows:
        if window["request_end"] < window["day_end"]:
            summary["shortened"].append(window["day"].isoformat())
    if summary["skipped"] or summary["shortened"]:
        logger.warning(
            "%s",
            json.dumps(
                {
                    "watermark": summary["watermark"],
                    "skipped": summary["skipped"],
                    "shortened": summary["shortened"],
                    "warning": "the hourly read is behind the re-read's range",
                }
            ),
        )

    ask_each_day_once(conn, windows, watermark, summary)
    logger.info("%s", json.dumps(summary))
    raise_for_reread_failures(summary)
    return summary


def new_reread_summary(last_day):
    return {
        "task": "reread",
        "last_day": last_day.isoformat(),
        "held": False,
        "watermark": None,
        "skipped": [],
        "shortened": [],
        "asked": [],
        "served": {},
        "left_out": {},
        "newly_stored": {},
        "seconds_late": {},
        "quarantined": [],
        "failed": {},
        "stopped_at": None,
        "not_asked": [],
    }


def ask_each_day_once(conn, windows, watermark, summary):
    """Ask each window in turn, a pause apart. A failed day is rolled back
    and the next is asked; HTTP 429 or a database error stops at once."""

    for position, window in enumerate(windows):
        if position > 0:
            time.sleep(REREAD_PAUSE_SECONDS)
        day_text = window["day"].isoformat()
        summary["asked"].append(day_text)
        try:
            ask_publish_day(conn, window, watermark, summary)
        except Exception as error:
            conn.rollback()
            reason = failure_reason(error)
            summary["failed"][day_text] = reason
            logger.warning(
                "%s",
                json.dumps({"day": day_text, "failed": reason, "error": str(error)}),
            )
            if stops_the_reread(error):
                summary["stopped_at"] = day_text
                for later_window in windows[position + 1 :]:
                    summary["not_asked"].append(later_window["day"].isoformat())
                return


def ask_publish_day(conn, window, watermark, summary):
    """Fetch one window, store the rows inside it, and record the counts."""

    day_text = window["day"].isoformat()
    retrieved_at = request_time()
    rows = fetch(window["start"], window["request_end"])
    if not isinstance(rows, list):
        raise RuntimeError(
            "Invalid response returned from Elexon REMIT API: expected a list"
        )
    kept, left_out = rows_in_window(rows, window["start"], window["day_end"], watermark)
    summary["served"][day_text] = len(rows)
    summary["left_out"][day_text] = left_out
    rejected_count = 0
    if kept:
        rejected_count = process_rows(
            kept,
            spec=REMIT_SPEC,
            dataset="REMIT",
            conn=conn,
            retrieved_at=retrieved_at,
            request_context={
                "publishDateTimeFrom": window["start"].isoformat(),
                "publishDateTimeTo": window["request_end"].isoformat(),
            },
            parse_rows=parse,
            load_rows=load,
            payload_dumps=decimal_json_dumps,
            log_missing_optional_fields=False,
        )
    if rejected_count:
        summary["quarantined"].append(day_text)

    stored = newly_stored(conn, window["start"], window["day_end"], retrieved_at)
    conn.rollback()
    summary["newly_stored"][day_text] = stored["rows"]
    if stored["rows"]:
        summary["seconds_late"][day_text] = [
            stored["smallest_seconds_late"],
            stored["largest_seconds_late"],
        ]
        logger.warning(
            "%s",
            json.dumps(
                {
                    "day": day_text,
                    "newly_stored": stored["rows"],
                    "smallest_seconds_late": stored["smallest_seconds_late"],
                    "largest_seconds_late": stored["largest_seconds_late"],
                }
            ),
        )


def newly_stored(conn, window_start, day_end, retrieved_at):
    """The rows of the window this request stored, and how many seconds after
    its publish time the earliest and latest of them was stored."""

    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT count(*), "
            "min(extract(epoch FROM retrieved_at - publish_time))::bigint, "
            "max(extract(epoch FROM retrieved_at - publish_time))::bigint "
            "FROM raw.elexon_remit "
            "WHERE publish_time >= %s AND publish_time < %s AND retrieved_at = %s",
            (window_start, day_end, retrieved_at),
        )
        row_count, smallest, largest = cursor.fetchone()
    return {
        "rows": row_count,
        "smallest_seconds_late": smallest,
        "largest_seconds_late": largest,
    }


def failure_reason(error):
    """The HTTP status or the kind of error, without its message."""

    status = http_status(error)
    if status is not None:
        return f"HTTP {status}"
    return type(error).__name__


def stops_the_reread(error):
    """HTTP 429 means Elexon asks for less; after a database error every later
    day would fail the same way."""

    if http_status(error) == TOO_MANY_REQUESTS:
        return True
    return isinstance(error, psycopg.Error)


def http_status(error):
    if not isinstance(error, requests.HTTPError):
        return None
    if error.response is None:
        return None
    return error.response.status_code


def raise_for_reread_failures(summary):
    """A quarantined row fails the task without a retry; a stop or any other
    failed day fails it too. The task has no retries either way."""

    if summary["quarantined"]:
        raise ContractRejected(
            f"REMIT rows quarantined by the re-read on {', '.join(summary['quarantined'])}"
            f"; failed days: {json.dumps(summary['failed'])}"
        )
    if summary["stopped_at"] is not None:
        raise RuntimeError(
            f"REMIT re-read stopped at {summary['stopped_at']} "
            f"({summary['failed'][summary['stopped_at']]}); not asked: "
            f"{', '.join(summary['not_asked']) or 'none'}"
        )
    if summary["failed"]:
        raise RuntimeError(
            f"REMIT re-read days failed: {json.dumps(summary['failed'])}"
        )


def iso_dates(days):
    texts = []
    for day in days:
        texts.append(day.isoformat())
    return texts
