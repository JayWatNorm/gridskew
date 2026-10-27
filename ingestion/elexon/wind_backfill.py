"""Load a year of wind forecast (WINDFOR) and outturn (FUELHH WIND), once.

A bounded one-off run with no DAG, one request at a time, a second apart:
WINDFOR first, one UTC publish day per request, then FUELHH, one settlement
date per request. WINDFOR is issued eight times a day; in the summer and
winter samples all eight fell inside the UTC day, so a publish day is held
when it stores at least 8 distinct publish times. A settlement date of FUELHH WIND
is held when it stores exactly its calendar's 46, 48 or 50 distinct periods.
A held day or date is skipped; any other, including one a stopped run left
short, is fetched again on the next run.

The first failure stops the run before another request: a rate limit
(HTTP 429), a server error (HTTP 5xx), a quarantined row, or anything else.
Elexon publishes no request rate and returns no rate-limit headers, so going
on after a refusal or an unexplained failure could only make it worse; a
re-run resumes from the held-day check. The exit code is 1 after a stop. The
report lists, per dataset, the days requested, held, failed and still not
held.

Run in the Airflow container, which holds the gridskew_prod connection:
    python -m ingestion.elexon.wind_backfill --dry-run
    python -m ingestion.elexon.wind_backfill
"""

import argparse
import json
import sys
import time
from datetime import date, datetime, timedelta, timezone

import requests

from ingestion.database import connect_to_gridskew_prod
from ingestion.elexon import wind_poller
from ingestion.elexon.settlement_calendar import expected_period_count
from ingestion.routing import ContractRejected

# Two days before the first settlement date read (22 August 2025), so the
# last forecast issued before its first half-hour is held.
FIRST_WINDFOR_PUBLISH_DAY = date(2025, 8, 20)
# The last settlement date read (16 August 2026): its last half-hour starts at
# 22:30 UTC, so the last issue before it is published that UTC day.
LAST_WINDFOR_PUBLISH_DAY = date(2026, 8, 16)
FIRST_FUELHH_SETTLEMENT_DATE = date(2025, 8, 22)
LAST_FUELHH_SETTLEMENT_DATE = date(2026, 8, 16)
ISSUES_PER_PUBLISH_DAY = 8
PAUSE_SECONDS = 1


def days_from(first, last):
    """Every day from first to last, both included."""

    days = []
    day = first
    while day <= last:
        days.append(day)
        day += timedelta(days=1)
    return days


def days_by_dataset():
    """The WINDFOR publish days and the FUELHH settlement dates to hold."""

    return {
        "WINDFOR": days_from(FIRST_WINDFOR_PUBLISH_DAY, LAST_WINDFOR_PUBLISH_DAY),
        "FUELHH": days_from(FIRST_FUELHH_SETTLEMENT_DATE, LAST_FUELHH_SETTLEMENT_DATE),
    }


def publish_window(day):
    """The UTC publish window of one day: its midnight to the next."""

    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    return start, start + timedelta(days=1)


def held_windfor_days(conn, days):
    """The publish days among `days` that store every issue."""

    first_start, _ = publish_window(min(days))
    _, last_end = publish_window(max(days))
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT (publish_time AT TIME ZONE 'UTC')::date AS publish_day, "
            "count(DISTINCT publish_time) "
            "FROM raw.elexon_windfor "
            "WHERE publish_time >= %s AND publish_time < %s "
            "GROUP BY publish_day",
            (first_start, last_end),
        )
        stored_issues = cursor.fetchall()
    conn.rollback()
    return days_with_every_issue(stored_issues) & set(days)


def days_with_every_issue(stored_issues):
    """The days whose count of distinct publish times reaches a whole day's."""

    held = set()
    for publish_day, issue_count in stored_issues:
        if issue_count >= ISSUES_PER_PUBLISH_DAY:
            held.add(publish_day)
    return held


def held_fuelhh_dates(conn, settlement_dates):
    """The settlement dates among `settlement_dates` that store every period."""

    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT settlement_date, count(DISTINCT settlement_period) "
            "FROM raw.elexon_fuelhh "
            "WHERE fuel_type = %s AND settlement_date BETWEEN %s AND %s "
            "GROUP BY settlement_date",
            (wind_poller.WIND_FUEL_TYPE, min(settlement_dates), max(settlement_dates)),
        )
        stored_periods = cursor.fetchall()
    conn.rollback()
    return dates_with_every_period(stored_periods) & set(settlement_dates)


def dates_with_every_period(stored_periods):
    """The dates whose count of distinct periods equals their calendar's."""

    held = set()
    for settlement_date, period_count in stored_periods:
        if period_count == expected_period_count(settlement_date):
            held.add(settlement_date)
    return held


def fetch_windfor_day(conn, day):
    publish_from, publish_to = publish_window(day)
    return wind_poller.run_windfor(conn, publish_from, publish_to)


def fetch_fuelhh_date(conn, settlement_date):
    return wind_poller.run_fuelhh(conn, settlement_date)


DATASETS = {
    "WINDFOR": (held_windfor_days, fetch_windfor_day),
    "FUELHH": (held_fuelhh_dates, fetch_fuelhh_date),
}


def held_by_dataset(conn, days_to_hold):
    held = {}
    for dataset, days in days_to_hold.items():
        held_days, _ = DATASETS[dataset]
        held[dataset] = held_days(conn, days)
    return held


def days_to_fetch(days, held):
    pending = []
    for day in days:
        if day not in held:
            pending.append(day)
    return pending


def iso_dates(days):
    texts = []
    for day in days:
        texts.append(day.isoformat())
    return texts


def plan(conn, days_to_hold):
    """What a run would fetch, without a request."""

    held = held_by_dataset(conn, days_to_hold)
    summary = {}
    requests_to_make = 0
    for dataset, days in days_to_hold.items():
        pending = days_to_fetch(days, held[dataset])
        requests_to_make += len(pending)
        summary[dataset] = {
            "first_day": min(days).isoformat(),
            "last_day": max(days).isoformat(),
            "days": len(days),
            "held": len(held[dataset]),
            "days_to_fetch": iso_dates(pending),
        }
    summary["requests_to_make"] = requests_to_make
    return summary


def request_queue(days_to_hold, held):
    """Every (dataset, day) not yet held, in fetch order."""

    queue = []
    for dataset, days in days_to_hold.items():
        for day in days_to_fetch(days, held[dataset]):
            queue.append((dataset, day))
    return queue


def http_status(error):
    if not isinstance(error, requests.HTTPError):
        return None
    if error.response is None:
        return None
    return error.response.status_code


def reason_for_stopping(error):
    """A short label for why a request failed, which tells the operator what
    to check before running again."""

    if isinstance(error, ContractRejected):
        return "quarantined rows"
    status = http_status(error)
    if status == 429:
        return "rate limited (HTTP 429)"
    if status is None:
        return "other failure"
    if status >= 500:
        return f"server error (HTTP {status})"
    return f"request refused (HTTP {status})"


def backfill(conn, days_to_hold, pause_seconds=PAUSE_SECONDS):
    """Fetch every day not yet held, stopping at the first failure; return a
    report."""

    held_before = held_by_dataset(conn, days_to_hold)
    outcomes = {}
    for dataset in days_to_hold:
        outcomes[dataset] = {"requested": [], "fetched": 0, "rows": 0, "failed": {}}

    first_request = True
    for dataset, day in request_queue(days_to_hold, held_before):
        if not first_request:
            time.sleep(pause_seconds)
        first_request = False
        _, fetch_day = DATASETS[dataset]
        outcome = outcomes[dataset]
        outcome["requested"].append(day.isoformat())
        try:
            summary = fetch_day(conn, day)
        except Exception as error:
            conn.rollback()
            reason = reason_for_stopping(error)
            outcome["failed"][day.isoformat()] = f"{reason}: {error}"
            print(json.dumps(stop_notice(dataset, day, reason, error)))
            break
        outcome["fetched"] += 1
        outcome["rows"] += summary["rows"]
        print(json.dumps(summary))

    held_after = held_by_dataset(conn, days_to_hold)
    return report(days_to_hold, held_before, held_after, outcomes)


def stop_notice(dataset, day, reason, error):
    return {
        "stopped": f"Back-fill stopped at {dataset} {day.isoformat()}: {reason}. "
        "No further request was made.",
        "dataset": dataset,
        "day": day.isoformat(),
        "reason": reason,
        "error": str(error),
    }


def report(days_to_hold, held_before, held_after, outcomes):
    backfill_report = {}
    for dataset, days in days_to_hold.items():
        outcome = outcomes[dataset]
        backfill_report[dataset] = {
            "first_day": min(days).isoformat(),
            "last_day": max(days).isoformat(),
            "days": len(days),
            "skipped_as_held": len(held_before[dataset]),
            "requested": outcome["requested"],
            "fetched": outcome["fetched"],
            "rows": outcome["rows"],
            "failed": outcome["failed"],
            "held_after_run": len(held_after[dataset]),
            "not_held_after_run": iso_dates(days_to_fetch(days, held_after[dataset])),
        }
    return backfill_report


def exit_code(backfill_report):
    for dataset_report in backfill_report.values():
        if dataset_report["failed"]:
            return 1
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the days each dataset would fetch; no request",
    )
    arguments = parser.parse_args(argv)
    days_to_hold = days_by_dataset()
    conn = connect_to_gridskew_prod()
    try:
        if arguments.dry_run:
            print(json.dumps(plan(conn, days_to_hold)))
            return 0
        backfill_report = backfill(conn, days_to_hold)
    finally:
        conn.close()
    print(json.dumps(backfill_report))
    return exit_code(backfill_report)


if __name__ == "__main__":
    sys.exit(main())
