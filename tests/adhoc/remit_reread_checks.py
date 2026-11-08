"""Run the daily REMIT re-read and its warning test on the disposable database,
with Elexon faked.

Each check stores REMIT messages in years no fixture uses (2150 to 2152, and
2000 for the warning test, rolled back), so the hourly watermark it reads is
its own. check_database_writes runs these first, before any check leaves a
REMIT row in quarantine, and every row they commit is deleted at the end.
"""

import json
from datetime import date, datetime, timedelta, timezone
from datetime import time as time_of_day
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

from ingestion.database import insert_rows
from ingestion.elexon import remit_poller
from ingestion.elexon.remit_poller import (
    load,
    newest_stored_publish_time,
    newly_stored,
    parse,
    quarantined_row_count,
    reread,
)
from ingestion.source_api import (
    decimal_json_dumps,
    utc_from_text_to_the_minute,
    utc_from_text_with_seconds,
)
from tests.adhoc.check_analyses_compile import compile_analyses

FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent
    / "fixtures"
    / "elexon"
    / "remit_publications.json"
)
MRID_PREFIX = "CI-RR-"
SESSION_ZONES = ("UTC", "Europe/London", "Etc/GMT-14", "Etc/GMT+12")
LATE_WARNING_TEST = "warn_remit_stored_late"
REMIT_INSERT = (
    "INSERT INTO raw.elexon_remit (mrid, revision_number, publish_time, "
    "created_time, message_type, event_type, unavailability_type, event_status, "
    "asset_id, affected_unit, fuel_type, normal_capacity, available_capacity, "
    "unavailable_capacity, event_start_time, event_end_time, payload, "
    "retrieved_at) VALUES %s"
)
END_OF_MINUTE = timedelta(seconds=59)
HOURLY_CAPTURE_DELAY = timedelta(minutes=10)

# Outage and restart: the hourly read stopped at OUTAGE_WATERMARK.
OUTAGE_WATERMARK = datetime(2150, 10, 1, 12, 0, tzinfo=timezone.utc)
OUTAGE_LAST_DAY = date(2150, 11, 8)
HOURLY_RESTART = datetime(2150, 11, 9, 4, 30, tzinfo=timezone.utc)
RESTARTED_WATERMARK = datetime(2150, 11, 9, 4, 0, tzinfo=timezone.utc)
FIRST_REREAD_REQUEST = datetime(2150, 11, 9, 4, 40, 1, tzinfo=timezone.utc)
LATE_MESSAGE_DAY = date(2150, 11, 3)
# The hourly read stopped exactly at midnight starting 5 November 2151.
MIDNIGHT_WATERMARK = datetime(2151, 11, 5, 0, 0, tzinfo=timezone.utc)
MIDNIGHT_LAST_DAY = date(2151, 11, 8)
# A message dated after its own capture, which the watermark never counts.
FUTURE_DATED_PUBLISH = "2153-01-01T00:00:00Z"
FUTURE_DATED_CAPTURE = datetime(2100, 1, 1, tzinfo=timezone.utc)
# Two runs across a failure, and a malformed time, in 2152.
SEQUENCE_WATERMARK = datetime(2152, 11, 9, 4, 0, tzinfo=timezone.utc)
SEQUENCE_LAST_DAY = date(2152, 11, 8)
FAILING_DAY = date(2152, 11, 4)
MISSED_MESSAGE_DAY = date(2152, 11, 6)
MALFORMED_DAY = date(2152, 11, 7)

# Evaluation A of the warning test: the range is 16 January to 14 February 2000.
EVALUATION_A = {
    "remit_late_check_today": "2000-02-15",
    "remit_late_check_from": "2000-01-01",
}
# Evaluation B: the var binds, so the range is 17 to 19 January 2000.
EVALUATION_B = {
    "remit_late_check_today": "2000-01-20",
    "remit_late_check_from": "2000-01-17",
}
# (publish_time, retrieved_at), UTC.
WARNING_FIXTURE = (
    ("2000-01-15T23:59:59Z", "2000-01-16T03:00:00Z"),
    ("2000-01-16T00:00:00Z", "2000-01-16T02:00:01Z"),
    ("2000-01-17T12:00:00Z", "2000-01-18T12:00:00Z"),
    ("2000-02-01T10:00:00Z", "2000-02-01T12:00:00Z"),
    ("2000-02-01T11:00:00Z", "2000-02-01T13:00:01Z"),
    ("2000-02-05T08:00:00Z", "2000-02-05T08:30:00Z"),
    ("2000-02-14T23:59:59Z", "2000-02-15T02:00:00Z"),
    ("2000-02-15T00:00:00Z", "2000-02-15T03:00:00Z"),
)
# (publish_day, stored_late, messages, smallest and largest seconds late)
EXPECTED_A = [
    (date(2000, 1, 16), 1, 1, 7201, 7201),
    (date(2000, 1, 17), 1, 1, 86400, 86400),
    (date(2000, 2, 1), 1, 2, 7201, 7201),
    (date(2000, 2, 14), 1, 1, 7201, 7201),
]
EXPECTED_B = [(date(2000, 1, 17), 1, 1, 86400, 86400)]
# Evaluation C: the nightly's null evaluation day, today's UTC date; an early
# floor so that only the 30 days bind.
EVALUATION_C = {"remit_late_check_from": "2000-01-01"}


def message_template():
    rows = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"), parse_float=Decimal)
    return rows[0]


def message(publish_text, label):
    """A REMIT message published at publish_text, created two minutes earlier."""

    publish_time = utc_from_text_with_seconds(publish_text)
    created_text = (publish_time - timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        **message_template(),
        "mrid": f"{MRID_PREFIX}{label}",
        "publishTime": publish_text,
        "createdTime": created_text,
    }


def noon_of(day, label):
    return message(f"{day.isoformat()}T12:00:00Z", f"{label}-{day.isoformat()}")


def days_from(first_day, last_day):
    days = []
    day = first_day
    while day <= last_day:
        days.append(day)
        day += timedelta(days=1)
    return days


def store(conn, messages, retrieved_at):
    load(parse(messages, retrieved_at), conn)


def store_as_the_hourly_read(conn, messages):
    """Each message stored ten minutes after its publish time, as the hourly
    read does."""

    for one_message in messages:
        published = utc_from_text_with_seconds(one_message["publishTime"])
        store(conn, [one_message], published + HOURLY_CAPTURE_DELAY)


def watermark(conn):
    found = newest_stored_publish_time(conn)
    conn.commit()
    return found


def stored_retrieved_at(conn, label):
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT retrieved_at FROM raw.elexon_remit WHERE mrid = %s",
            (f"{MRID_PREFIX}{label}",),
        )
        found = cursor.fetchall()
    conn.commit()
    return found


def remit_quarantine_rows(conn):
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM raw.endpoint_quarantine WHERE dataset = 'REMIT'"
        )
        (row_count,) = cursor.fetchone()
    conn.commit()
    return row_count


def elexon_serving(served, statuses_by_day=None, extra_by_day=None):
    """Stand in for Elexon's REMIT stream: each request gets the served
    messages published in its window, both ends included to the minute; a
    status in statuses_by_day for the window's first day answers instead, and
    extra_by_day adds rows outside the window. Records each window asked."""

    if statuses_by_day is None:
        statuses_by_day = {}
    if extra_by_day is None:
        extra_by_day = {}
    asked = []

    def get(url, params, headers, timeout):
        window_start = utc_from_text_to_the_minute(params["publishDateTimeFrom"])
        window_end = utc_from_text_to_the_minute(params["publishDateTimeTo"])
        first_day = window_start.date()
        asked.append((params["publishDateTimeFrom"], params["publishDateTimeTo"]))
        response = MagicMock()
        status = statuses_by_day.get(first_day)
        if status is not None:
            response.status_code = status
            response.raise_for_status.side_effect = requests.HTTPError(
                f"{status} Error", response=response
            )
            return response
        body = []
        for one_message in served:
            published = utc_from_text_with_seconds(one_message["publishTime"])
            if window_start <= published <= window_end + END_OF_MINUTE:
                body.append(one_message)
        body.extend(extra_by_day.get(first_day, []))
        response.text = decimal_json_dumps(body)
        return response

    return get, asked


def clock_from(first_time):
    """A request clock that moves a minute per request from first_time."""

    readings = []

    def now():
        reading = first_time + timedelta(minutes=len(readings))
        readings.append(reading)
        return reading

    return now, readings


def run_reread(conn, last_day, get, first_request_time):
    """The real re-read on real tables, Elexon and the clock faked. Returns
    the summary or the error, and the request times."""

    now, readings = clock_from(first_request_time)
    with (
        patch("ingestion.elexon.remit_poller.requests.get", side_effect=get),
        patch("ingestion.elexon.remit_poller.time.sleep"),
        patch("ingestion.elexon.remit_poller.request_time", side_effect=now),
    ):
        try:
            outcome = reread(conn, last_day)
        except Exception as error:
            outcome = error
    return outcome, readings


def run_hourly(conn, get, run_time):
    """The real hourly run on real tables, Elexon and its clock faked."""

    with (
        patch("ingestion.elexon.remit_poller.requests.get", side_effect=get),
        patch("ingestion.elexon.remit_poller.time.sleep"),
        patch("ingestion.elexon.remit_poller.datetime") as mock_datetime,
    ):
        mock_datetime.now.return_value = run_time
        remit_poller.run(conn)


def asked_days(asked):
    days = []
    for publish_from, _publish_to in asked:
        days.append(date.fromisoformat(publish_from[:10]))
    return days


def delete_reread_check_rows(conn):
    conn.rollback()
    with conn.cursor() as cursor:
        cursor.execute(
            "DELETE FROM raw.elexon_remit WHERE mrid LIKE %s", (f"{MRID_PREFIX}%",)
        )
        cursor.execute(
            "DELETE FROM raw.endpoint_quarantine WHERE dataset = 'REMIT' "
            "AND payload ->> 'mrid' LIKE %s",
            (f"{MRID_PREFIX}%",),
        )
    conn.commit()


def check_no_remit_row_is_in_quarantine(conn):
    count = quarantined_row_count(conn)
    conn.commit()
    assert count == 0, (
        f"{count} REMIT rows are in quarantine before the re-read checks; "
        "they hold the re-read"
    )


def check_outage_and_restart(conn):
    """A re-read never moves the hourly watermark, whatever state the hourly
    read is in: after an outage it asks nothing, and after the restart it
    stores only a message the hourly read missed."""

    store_as_the_hourly_read(conn, [message("2150-10-01T12:00:00Z", "BEFORE-OUTAGE")])
    assert watermark(conn) == OUTAGE_WATERMARK, "a stored REMIT row is later than 2150"

    served = []
    for day in days_from(date(2150, 10, 2), OUTAGE_LAST_DAY):
        served.append(noon_of(day, "HOURLY"))
    served.append(message("2150-11-09T04:00:00Z", "NEWEST"))
    get, asked = elexon_serving(served)

    summary, _readings = run_reread(conn, OUTAGE_LAST_DAY, get, FIRST_REREAD_REQUEST)
    assert asked == [], f"the re-read asked {asked} while the hourly read was behind"
    assert summary["skipped"] == [
        day.isoformat() for day in days_from(date(2150, 11, 2), OUTAGE_LAST_DAY)
    ]
    assert watermark(conn) == OUTAGE_WATERMARK, "the re-read moved the watermark"
    assert stored_retrieved_at(conn, "HOURLY-2150-11-08") == []

    run_hourly(conn, get, HOURLY_RESTART)
    assert watermark(conn) == RESTARTED_WATERMARK
    assert len(stored_retrieved_at(conn, "HOURLY-2150-10-15")) == 1, (
        "the restarted hourly read did not store October"
    )
    hourly_capture = stored_retrieved_at(conn, "HOURLY-2150-11-03")

    late = message("2150-11-03T15:00:00Z", "LATE-2150-11-03")
    served.append(late)
    next_day_row = message("2150-11-09T00:00:00Z", "NEXT-DAY")
    after_watermark_row = message("2150-11-09T05:00:00Z", "AFTER-WATERMARK")
    get, asked = elexon_serving(
        served, extra_by_day={OUTAGE_LAST_DAY: [next_day_row, after_watermark_row]}
    )
    summary, readings = run_reread(conn, OUTAGE_LAST_DAY, get, FIRST_REREAD_REQUEST)

    assert asked_days(asked) == days_from(date(2150, 11, 2), OUTAGE_LAST_DAY)
    assert summary["newly_stored"] == {
        "2150-11-02": 0,
        "2150-11-03": 1,
        "2150-11-04": 0,
        "2150-11-05": 0,
        "2150-11-06": 0,
        "2150-11-07": 0,
        "2150-11-08": 0,
    }, summary
    assert summary["left_out"]["2150-11-08"] == 2, summary
    assert stored_retrieved_at(conn, "LATE-2150-11-03") == [(readings[1],)]
    assert stored_retrieved_at(conn, "HOURLY-2150-11-03") == hourly_capture
    assert stored_retrieved_at(conn, "NEXT-DAY") == []
    assert stored_retrieved_at(conn, "AFTER-WATERMARK") == []
    assert watermark(conn) == RESTARTED_WATERMARK, "the re-read moved the watermark"


def check_a_watermark_at_midnight_skips_its_day(conn):
    """With the hourly read stopped at midnight starting 5 November, the
    re-read asks 2, 3 and 4 November only. A message dated after its own
    capture is not the watermark."""

    store_as_the_hourly_read(conn, [message("2151-11-05T00:00:00Z", "MIDNIGHT")])
    store(conn, [message(FUTURE_DATED_PUBLISH, "FUTURE-DATED")], FUTURE_DATED_CAPTURE)
    assert watermark(conn) == MIDNIGHT_WATERMARK

    served = []
    for day in days_from(date(2151, 11, 2), MIDNIGHT_LAST_DAY):
        served.append(noon_of(day, "MIDNIGHT-CASE"))
    get, asked = elexon_serving(served)
    summary, _readings = run_reread(
        conn, MIDNIGHT_LAST_DAY, get, datetime(2151, 11, 9, 4, 40, tzinfo=timezone.utc)
    )

    assert asked_days(asked) == [
        date(2151, 11, 2),
        date(2151, 11, 3),
        date(2151, 11, 4),
    ]
    assert asked[-1] == ("2151-11-04T00:00Z", "2151-11-05T00:00Z")
    assert summary["skipped"] == [
        "2151-11-05",
        "2151-11-06",
        "2151-11-07",
        "2151-11-08",
    ]
    assert watermark(conn) == MIDNIGHT_WATERMARK, "the re-read moved the watermark"


def check_two_runs_across_a_failure_and_a_malformed_time(conn):
    """Run 1 fails one day and stores a missed message; run 2 asks all seven
    again, stores nothing twice and keeps run 1's stamp. A malformed time
    fails its day, stores no row of that response and quarantines nothing."""

    served = []
    for day in days_from(date(2152, 11, 2), SEQUENCE_LAST_DAY):
        served.append(noon_of(day, "SEQUENCE"))
    served.append(message("2152-11-09T04:00:00Z", "SEQUENCE-NEWEST"))
    store_as_the_hourly_read(conn, served)
    assert watermark(conn) == SEQUENCE_WATERMARK

    missed = message("2152-11-06T18:00:00Z", "MISSED")
    served.append(missed)
    get, asked = elexon_serving(served, statuses_by_day={FAILING_DAY: 503})
    first_request = datetime(2152, 11, 9, 4, 40, tzinfo=timezone.utc)
    error, readings = run_reread(conn, SEQUENCE_LAST_DAY, get, first_request)
    assert str(error) == 'REMIT re-read days failed: {"2152-11-04": "HTTP 503"}'
    assert len(asked) == 7
    run_1_stamp = stored_retrieved_at(conn, "MISSED")
    assert run_1_stamp == [(readings[4],)], run_1_stamp

    get, asked = elexon_serving(served)
    summary, _readings = run_reread(
        conn, SEQUENCE_LAST_DAY, get, first_request + timedelta(days=1)
    )
    assert len(asked) == 7
    assert sum(summary["newly_stored"].values()) == 0, summary
    assert stored_retrieved_at(conn, "MISSED") == run_1_stamp

    quarantine_before = remit_quarantine_rows(conn)
    companion = message("2152-11-07T20:00:00Z", "MALFORMED-COMPANION")
    malformed = {**message("2152-11-07T21:00:00Z", "MALFORMED"), "publishTime": "x"}
    get, asked = elexon_serving(
        served, extra_by_day={MALFORMED_DAY: [companion, malformed]}
    )
    error, _readings = run_reread(
        conn, SEQUENCE_LAST_DAY, get, first_request + timedelta(days=2)
    )
    assert str(error) == 'REMIT re-read days failed: {"2152-11-07": "ValueError"}'
    assert len(asked) == 7
    assert stored_retrieved_at(conn, "MALFORMED-COMPANION") == []
    assert remit_quarantine_rows(conn) == quarantine_before
    assert watermark(conn) == SEQUENCE_WATERMARK


def check_the_queries_agree_in_every_session_zone(connect):
    """The watermark and the newly-stored count read the same in UTC,
    Europe/London, Etc/GMT-14 and Etc/GMT+12 sessions."""

    found = {}
    for zone in SESSION_ZONES:
        conn = connect(zone)
        try:
            stored = newly_stored(
                conn,
                datetime(2152, 11, 6, tzinfo=timezone.utc),
                datetime(2152, 11, 7, tzinfo=timezone.utc),
                stored_retrieved_at(conn, "MISSED")[0][0],
            )
            found[zone] = (newest_stored_publish_time(conn), stored)
        finally:
            conn.rollback()
            conn.close()
    for zone, (zone_watermark, stored) in found.items():
        assert zone_watermark.astimezone(timezone.utc) == SEQUENCE_WATERMARK, zone
        assert stored["rows"] == 1, (zone, stored)
        assert stored["smallest_seconds_late"] == stored["largest_seconds_late"]
        assert stored == found["UTC"][1], (zone, stored)


def compiled_late_warning(dbt_vars):
    manifest = compile_analyses(
        dbt_vars, also_compiled=(LATE_WARNING_TEST,), selected=()
    )
    for node in manifest["nodes"].values():
        if node["resource_type"] == "test" and node["name"] == LATE_WARNING_TEST:
            return node["compiled_code"]
    raise AssertionError(f"dbt did not compile {LATE_WARNING_TEST}")


def store_warning_fixture_uncommitted(conn):
    rows = []
    for number, (publish_text, retrieved_text) in enumerate(WARNING_FIXTURE, start=1):
        one_message = message(publish_text, f"WARN-R{number}")
        retrieved_at = utc_from_text_with_seconds(retrieved_text)
        rows.extend(parse([one_message], retrieved_at))
    with conn.cursor() as cursor:
        insert_rows(cursor, REMIT_INSERT, rows)


def rows_the_warning_lists(conn, warning_sql):
    with conn.cursor() as cursor:
        cursor.execute(warning_sql)
        return [tuple(row) for row in cursor.fetchall()]


def check_the_late_warning_on_fixed_evaluation_days(connect):
    """The warning test lists exactly the expected rows for evaluations A and
    B, in every session zone. The rows are rolled back."""

    warnings = {
        "A": (compiled_late_warning(EVALUATION_A), EXPECTED_A),
        "B": (compiled_late_warning(EVALUATION_B), EXPECTED_B),
    }
    conn = connect(None)
    try:
        store_warning_fixture_uncommitted(conn)
        for name, (warning_sql, expected) in warnings.items():
            for zone in SESSION_ZONES:
                with conn.cursor() as cursor:
                    cursor.execute(f"SET TIME ZONE '{zone}'")
                listed = rows_the_warning_lists(conn, warning_sql)
                assert listed == expected, f"evaluation {name}, {zone}: {listed}"
    finally:
        conn.rollback()
        conn.close()


def store_rows_around_today_uncommitted(conn, utc_today):
    """Late rows on today's UTC date, on yesterday and 31 days back. Only
    yesterday's is in the range when the evaluation day is today's UTC
    date."""

    rows = []
    for label, publish_time in (
        ("TODAY", datetime.combine(utc_today, time_of_day(0, 0, 30), timezone.utc)),
        (
            "YESTERDAY",
            datetime.combine(utc_today, time_of_day(12), timezone.utc)
            - timedelta(days=1),
        ),
        (
            "DAY-31",
            datetime.combine(utc_today, time_of_day(12), timezone.utc)
            - timedelta(days=31),
        ),
    ):
        publish_text = publish_time.strftime("%Y-%m-%dT%H:%M:%SZ")
        one_message = message(publish_text, f"WARN-C-{label}")
        rows.extend(parse([one_message], publish_time + timedelta(hours=3)))
    with conn.cursor() as cursor:
        insert_rows(cursor, REMIT_INSERT, rows)


def database_utc_today(conn):
    with conn.cursor() as cursor:
        cursor.execute("SELECT (now() AT TIME ZONE 'UTC')::date")
        (utc_today,) = cursor.fetchone()
    return utc_today


def check_the_late_warning_counts_back_from_the_utc_date(connect):
    """With remit_late_check_today null, the range ends yesterday by the UTC
    date in every session zone: at any hour one of Etc/GMT-14 and
    Etc/GMT+12 is on another date than UTC. The rows are rolled back."""

    warning_sql = compiled_late_warning(EVALUATION_C)
    conn = connect(None)
    try:
        utc_today = database_utc_today(conn)
        store_rows_around_today_uncommitted(conn, utc_today)
        expected = [(utc_today - timedelta(days=1), 1, 1, 10800, 10800)]
        for zone in SESSION_ZONES:
            with conn.cursor() as cursor:
                cursor.execute(f"SET TIME ZONE '{zone}'")
            listed = rows_the_warning_lists(conn, warning_sql)
            assert listed == expected, f"evaluation C, {zone}: {listed}"
    finally:
        conn.rollback()
        conn.close()


def run_remit_reread_checks(writer, connect):
    """Every re-read check; connect(zone) opens a disposable connection."""

    delete_reread_check_rows(writer)
    try:
        check_no_remit_row_is_in_quarantine(writer)
        check_outage_and_restart(writer)
        check_a_watermark_at_midnight_skips_its_day(writer)
        check_two_runs_across_a_failure_and_a_malformed_time(writer)
        check_the_queries_agree_in_every_session_zone(connect)
        check_the_late_warning_on_fixed_evaluation_days(connect)
        check_the_late_warning_counts_back_from_the_utc_date(connect)
    finally:
        delete_reread_check_rows(writer)
    print(
        "REMIT re-read: never moves the hourly watermark (outage and restart, "
        "midnight, two runs across a failure); a malformed time fails its day "
        "without a quarantine row; the late warning lists its literal rows in "
        "four session zones"
    )
