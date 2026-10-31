"""Fetch, validate, quarantine and load Elexon wind forecast and outturn.

WINDFOR is the national wind generation forecast: up to eight issues a day,
each forecasting hourly points. FUELHH is the half-hourly outturn by fuel
type; only WIND is asked for. A WINDFOR request is one publish-time window;
a FUELHH request is one settlement date. Every issue and every publication is
kept, so fetching a window or a date again inserts nothing.

The daily capture asks for days from FIRST_CAPTURE_DAY on. Its target task
asks for one day, the run's target, unless it is complete or held in
quarantine. Its look-back task asks once for each of the seven days before
the target that is neither, newest first, and carries on past a failed
request; it stops at once on HTTP 429 or a database error.
"""

import json
import logging
import time
from datetime import date, datetime, timedelta, timezone

import psycopg
import requests

from ingestion.database import insert_rows
from ingestion.elexon.contracts import FUELHH_SPEC, WINDFOR_SPEC
from ingestion.elexon.settlement_calendar import LONDON, expected_period_count
from ingestion.routing import ContractRejected, process_rows
from ingestion.source_api import (
    USER_AGENT,
    utc_from_text_with_seconds,
    utc_text_to_the_minute,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://data.elexon.co.uk/bmrs/api/v1/datasets"
WIND_FUEL_TYPE = "WIND"

# Q36's reading rule holds every day from at least four weeks before its window
# opens; four weeks before is 3 November 2026 at the earliest.
FIRST_CAPTURE_DAY = date(2026, 11, 1)
LOOK_BACK_DAYS = 7
ISSUES_PER_PUBLISH_DAY = 8
# Each issue forecasts every hour from 21:00 UK time on the day before its
# publish day to 21:00 UK time two days after it.
ISSUE_SPAN_HOUR = 21
HOUR = timedelta(hours=1)
PAUSE_SECONDS = 1
TOO_MANY_REQUESTS = 429


def fetch_windfor(publish_from, publish_to):
    """Fetch every WINDFOR issue published in a window."""

    response = requests.get(
        f"{BASE_URL}/WINDFOR",
        params={
            "publishDateTimeFrom": utc_text_to_the_minute(publish_from),
            "publishDateTimeTo": utc_text_to_the_minute(publish_to),
        },
        headers={"User-Agent": USER_AGENT},
        timeout=60,
    )
    response.raise_for_status()
    return response.json()


def fetch_fuelhh(settlement_date):
    """Fetch the FUELHH wind outturn of one settlement date."""

    response = requests.get(
        f"{BASE_URL}/FUELHH",
        params={
            "settlementDateFrom": settlement_date.isoformat(),
            "settlementDateTo": settlement_date.isoformat(),
            "fuelType": WIND_FUEL_TYPE,
        },
        headers={"User-Agent": USER_AGENT},
        timeout=60,
    )
    response.raise_for_status()
    return response.json()


def run_windfor(conn, publish_from, publish_to):
    """Load one publish window of WINDFOR; return a summary.

    A window with no issue is valid and loads nothing. A row the contract
    rejects is quarantined and then no row of the window is loaded.
    """

    retrieved_at = datetime.now(timezone.utc)
    body = fetch_windfor(publish_from, publish_to)
    request_context = {
        "publishDateTimeFrom": publish_from.isoformat(),
        "publishDateTimeTo": publish_to.isoformat(),
    }
    rows = load_response(conn, "WINDFOR", body, retrieved_at, request_context)

    summary = {
        "dataset": "WINDFOR",
        "publish_from": publish_from.isoformat(),
        "publish_to": publish_to.isoformat(),
        "rows": len(rows),
        "issues": len(distinct_values(rows, "publishTime")),
    }
    logger.info("%s", json.dumps(summary))
    return summary


def run_fuelhh(conn, settlement_date):
    """Load the FUELHH wind outturn of one settlement date; return a summary.

    A date with no row is valid and loads nothing. A row the contract rejects
    is quarantined and then no row of the date is loaded.
    """

    retrieved_at = datetime.now(timezone.utc)
    body = fetch_fuelhh(settlement_date)
    request_context = {
        "settlementDate": settlement_date.isoformat(),
        "fuelType": WIND_FUEL_TYPE,
    }
    rows = load_response(conn, "FUELHH", body, retrieved_at, request_context)

    summary = {
        "dataset": "FUELHH",
        "settlement_date": settlement_date.isoformat(),
        "rows": len(rows),
        "periods": len(distinct_values(rows, "settlementPeriod")),
    }
    logger.info("%s", json.dumps(summary))
    return summary


def load_response(conn, dataset, body, retrieved_at, request_context):
    """Check the envelope, then validate, quarantine and load its rows whole.

    Returns the rows.
    """

    spec, parse, load = DATASETS[dataset]
    rows = None
    if isinstance(body, dict):
        rows = body.get("data")
    if not isinstance(rows, list):
        raise RuntimeError(
            f"Invalid response returned from Elexon {dataset} API: expected a data list"
        )
    if not rows:
        return rows

    rejected_count = process_rows(
        rows,
        spec=spec,
        dataset=dataset,
        conn=conn,
        retrieved_at=retrieved_at,
        request_context=request_context,
        parse_rows=parse,
        load_rows=load,
        load_all_or_none=True,
    )
    if rejected_count:
        raise ContractRejected(
            f"Quarantined {rejected_count} rows due to validation errors"
        )
    return rows


def distinct_values(rows, field):
    values = set()
    for row in rows:
        values.add(row[field])
    return values


def parse_windfor(source_rows, retrieved_at):
    """Convert compatible source dictionaries to typed WINDFOR insert tuples."""

    parsed_rows = []
    for source_row in source_rows:
        parsed_rows.append(
            (
                utc_from_text_with_seconds(source_row["startTime"]),
                utc_from_text_with_seconds(source_row["publishTime"]),
                source_row["generation"],
                retrieved_at,
            )
        )
    return parsed_rows


def parse_fuelhh(source_rows, retrieved_at):
    """Convert compatible source dictionaries to typed FUELHH insert tuples."""

    parsed_rows = []
    for source_row in source_rows:
        parsed_rows.append(
            (
                date.fromisoformat(source_row["settlementDate"]),
                source_row["settlementPeriod"],
                utc_from_text_with_seconds(source_row["startTime"]),
                source_row["fuelType"],
                utc_from_text_with_seconds(source_row["publishTime"]),
                source_row["generation"],
                retrieved_at,
            )
        )
    return parsed_rows


def load_windfor(parsed_rows, conn):
    """Insert typed WINDFOR rows, ignoring issues already stored, and commit."""

    insert_sql = (
        "INSERT INTO raw.elexon_windfor (start_time, publish_time, generation, "
        "retrieved_at) VALUES %s "
        "ON CONFLICT (start_time, publish_time) DO NOTHING"
    )

    with conn.cursor() as cursor:
        insert_rows(cursor, insert_sql, parsed_rows)
    conn.commit()


def load_fuelhh(parsed_rows, conn):
    """Insert typed FUELHH rows, ignoring publications already stored, and commit."""

    insert_sql = (
        "INSERT INTO raw.elexon_fuelhh (settlement_date, settlement_period, "
        "start_time, fuel_type, publish_time, generation, retrieved_at) VALUES %s "
        "ON CONFLICT (settlement_date, settlement_period, fuel_type, publish_time) "
        "DO NOTHING"
    )

    with conn.cursor() as cursor:
        insert_rows(cursor, insert_sql, parsed_rows)
    conn.commit()


DATASETS = {
    "WINDFOR": (WINDFOR_SPEC, parse_windfor, load_windfor),
    "FUELHH": (FUELHH_SPEC, parse_fuelhh, load_fuelhh),
}


def expected_points_per_issue(publish_day):
    """The hourly points a whole issue of publish_day holds: one for each hour
    from 21:00 UK time the day before to 21:00 UK time two days after, both
    included. 73, or 72 and 74 when the span holds a clock change."""

    first_point = uk_evening_in_utc(publish_day - timedelta(days=1))
    last_point = uk_evening_in_utc(publish_day + timedelta(days=2))
    return int((last_point - first_point) / HOUR) + 1


def uk_evening_in_utc(day):
    evening = datetime(day.year, day.month, day.day, ISSUE_SPAN_HOUR, tzinfo=LONDON)
    return evening.astimezone(timezone.utc)


def windfor_days_complete(stored_issue_points):
    """The publish days that store at least 8 issues, every one of them with at
    least its day's expected points.

    stored_issue_points holds one (publish_day, publish_time, points) row per
    stored issue, as windfor_issue_points returns them.
    """

    issue_count_by_day = {}
    days_with_a_short_issue = set()
    for publish_day, _publish_time, points in stored_issue_points:
        issue_count_by_day[publish_day] = issue_count_by_day.get(publish_day, 0) + 1
        if points < expected_points_per_issue(publish_day):
            days_with_a_short_issue.add(publish_day)

    complete = set()
    for publish_day, issue_count in issue_count_by_day.items():
        if issue_count < ISSUES_PER_PUBLISH_DAY:
            continue
        if publish_day in days_with_a_short_issue:
            continue
        complete.add(publish_day)
    return complete


def fuelhh_dates_complete(stored_periods):
    """The settlement dates whose stored WIND periods are exactly 1 to the
    date's 46, 48 or 50. A period published twice counts once.

    stored_periods holds one (settlement_date, settlement_period) row per
    stored publication, as fuelhh_stored_periods returns them.
    """

    periods_by_date = {}
    for settlement_date, settlement_period in stored_periods:
        if settlement_date not in periods_by_date:
            periods_by_date[settlement_date] = set()
        periods_by_date[settlement_date].add(settlement_period)

    complete = set()
    for settlement_date, periods in periods_by_date.items():
        period_count = expected_period_count(settlement_date)
        if len(periods) != period_count:
            continue
        if min(periods) != 1:
            continue
        if max(periods) != period_count:
            continue
        complete.add(settlement_date)
    return complete


def target_days(target_day):
    """The target as the target task's days: none before FIRST_CAPTURE_DAY."""

    if target_day < FIRST_CAPTURE_DAY:
        return []
    return [target_day]


def look_back_days(target_day):
    """The days the look-back considers, newest first: the target's seven
    days before it, none before FIRST_CAPTURE_DAY."""

    days = []
    for days_before in range(1, LOOK_BACK_DAYS + 1):
        day = target_day - timedelta(days=days_before)
        if day < FIRST_CAPTURE_DAY:
            break
        days.append(day)
    return days


def days_to_ask(days, complete_days, held_days):
    """The days neither complete nor held in quarantine, in their given order."""

    asked = []
    for day in days:
        if day in complete_days:
            continue
        if day in held_days:
            continue
        asked.append(day)
    return asked


def capture_windfor_target(conn, target_day):
    """Ask for the target publish day, unless it is complete or held."""

    return capture(conn, "WINDFOR", "target", target_day, target_days(target_day))


def capture_windfor_look_back(conn, target_day):
    """Ask once for each publish day of the look-back not complete or held."""

    return capture(conn, "WINDFOR", "look_back", target_day, look_back_days(target_day))


def capture_fuelhh_target(conn, target_day):
    """Ask for the target settlement date, unless it is complete or held."""

    return capture(conn, "FUELHH", "target", target_day, target_days(target_day))


def capture_fuelhh_look_back(conn, target_day):
    """Ask once for each settlement date of the look-back not complete or
    held."""

    return capture(conn, "FUELHH", "look_back", target_day, look_back_days(target_day))


def capture(conn, dataset, task, target_day, days):
    """Ask once for each of days that is neither complete nor held, in order;
    log the summary; then fail if a day is held or was quarantined, or a
    request failed. Returns the summary."""

    summary = new_summary(dataset, task, target_day, days)
    if days:
        complete = complete_days(conn, dataset, days)
        held = held_days(conn, dataset, days)
        conn.rollback()
        summary["held"] = iso_dates(sorted(held, reverse=True))
        ask_each_once(conn, dataset, days_to_ask(days, complete, held), summary)
        complete_after = complete_days(conn, dataset, days)
        conn.rollback()
        record_days_not_complete(summary, days, complete_after, target_day)
    logger.info("%s", json.dumps(summary))
    raise_for_failures(dataset, summary)
    return summary


def new_summary(dataset, task, target_day, days):
    first_day = None
    last_day = None
    if days:
        first_day = min(days).isoformat()
        last_day = max(days).isoformat()
    return {
        "dataset": dataset,
        "task": task,
        "target": target_day.isoformat(),
        "first_day": first_day,
        "last_day": last_day,
        "asked": [],
        "rows_returned": {},
        "failed": {},
        "quarantined": [],
        "held": [],
        "not_complete": [],
        "leaving_not_complete": None,
    }


def ask_each_once(conn, dataset, days, summary):
    """Ask for each day in turn, a pause apart. A failed request is logged and
    rolled back, and the next day is asked; HTTP 429 or a database error stops
    the asking at once."""

    ask_day = ASK_DAY[dataset]
    for request_number, day in enumerate(days):
        if request_number > 0:
            time.sleep(PAUSE_SECONDS)
        summary["asked"].append(day.isoformat())
        try:
            reply = ask_day(conn, day)
        except ContractRejected:
            conn.rollback()
            summary["quarantined"].append(day.isoformat())
            continue
        except Exception as error:
            conn.rollback()
            reason = failure_reason(error)
            summary["failed"][day.isoformat()] = reason
            logger.warning(
                "%s",
                json.dumps(
                    {"dataset": dataset, "day": day.isoformat(), "failed": reason}
                ),
            )
            if stops_the_asking(error):
                break
            continue
        summary["rows_returned"][day.isoformat()] = reply["rows"]


def failure_reason(error):
    status = http_status(error)
    if status is not None:
        return f"HTTP {status}"
    if isinstance(error, psycopg.Error):
        return f"database error: {error}"
    return f"{type(error).__name__}: {error}"


def stops_the_asking(error):
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


def record_days_not_complete(summary, days, complete, target_day):
    """List the days still not complete, and warn of the day leaving the
    look-back not complete: the schedule will not ask for it again."""

    for day in days:
        if day not in complete:
            summary["not_complete"].append(day.isoformat())

    leaving_day = target_day - timedelta(days=LOOK_BACK_DAYS)
    if leaving_day not in days or leaving_day in complete:
        return
    summary["leaving_not_complete"] = leaving_day.isoformat()
    logger.warning(
        "%s",
        json.dumps(
            {
                "dataset": summary["dataset"],
                "day": leaving_day.isoformat(),
                "warning": "leaves the look-back not complete; no scheduled run "
                "asks for it again",
            }
        ),
    )


def raise_for_failures(dataset, summary):
    """A day held or quarantined fails the task without a retry; any other
    failed request fails it so Airflow may retry."""

    in_quarantine = sorted(set(summary["held"]) | set(summary["quarantined"]))
    if in_quarantine:
        raise ContractRejected(
            f"{dataset} days held in quarantine, not asked until their rows in "
            f"raw.endpoint_quarantine are dealt with: {', '.join(in_quarantine)}"
        )
    if summary["failed"]:
        raise RuntimeError(
            f"{dataset} requests failed: {json.dumps(summary['failed'])}"
        )


def iso_dates(days):
    texts = []
    for day in days:
        texts.append(day.isoformat())
    return texts


def utc_day_window(day):
    """A UTC day as a publish window: its midnight to the next."""

    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    return start, start + timedelta(days=1)


def ask_windfor_day(conn, publish_day):
    publish_from, publish_to = utc_day_window(publish_day)
    return run_windfor(conn, publish_from, publish_to)


def complete_days(conn, dataset, days):
    """The days among `days` the stored rows hold complete."""

    stored_rows, days_complete = COMPLETENESS[dataset]
    return days_complete(stored_rows(conn, min(days), max(days)))


def windfor_issue_points(conn, first_day, last_day):
    """One (publish_day, publish_time, points) row per stored WINDFOR issue
    stamped on the UTC days from first_day to last_day."""

    window_start, _ = utc_day_window(first_day)
    _, window_end = utc_day_window(last_day)
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT (publish_time AT TIME ZONE 'UTC')::date, publish_time, count(*) "
            "FROM raw.elexon_windfor "
            "WHERE publish_time >= %s AND publish_time < %s "
            "GROUP BY publish_time",
            (window_start, window_end),
        )
        return cursor.fetchall()


def fuelhh_stored_periods(conn, first_day, last_day):
    """One (settlement_date, settlement_period) row per stored FUELHH WIND
    publication of the settlement dates from first_day to last_day."""

    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT settlement_date, settlement_period FROM raw.elexon_fuelhh "
            "WHERE fuel_type = %s AND settlement_date BETWEEN %s AND %s",
            (WIND_FUEL_TYPE, first_day, last_day),
        )
        return cursor.fetchall()


def held_days(conn, dataset, days):
    """The days among `days` named by a row of the dataset's in
    raw.endpoint_quarantine. Asking again would quarantine the same rows."""

    context_field, day_named = QUARANTINE_CONTEXT[dataset]
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT DISTINCT request_context ->> %s FROM raw.endpoint_quarantine "
            "WHERE dataset = %s",
            (context_field, dataset),
        )
        named = cursor.fetchall()
    held = set()
    for (context_value,) in named:
        held.add(day_named(context_value))
    return held & set(days)


def utc_day_of(instant_text):
    """The UTC date of an ISO instant with an offset, such as
    2026-11-05T00:00:00+00:00."""

    return datetime.fromisoformat(instant_text).astimezone(timezone.utc).date()


COMPLETENESS = {
    "WINDFOR": (windfor_issue_points, windfor_days_complete),
    "FUELHH": (fuelhh_stored_periods, fuelhh_dates_complete),
}
# The request_context field that names the day a quarantined row came from.
QUARANTINE_CONTEXT = {
    "WINDFOR": ("publishDateTimeFrom", utc_day_of),
    "FUELHH": ("settlementDate", date.fromisoformat),
}
ASK_DAY = {
    "WINDFOR": ask_windfor_day,
    "FUELHH": run_fuelhh,
}
