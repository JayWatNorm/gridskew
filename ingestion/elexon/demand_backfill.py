"""Load a year of demand forecast (NDF) and outturn (INDO) history, once.

A bounded one-off run with no DAG: one UTC publish day of one dataset per
request, a second apart, NDF first and then INDO. Both datasets are issued
every half-hour, so a whole publish day holds 48 distinct publish times, clock
change or not. A day with at least 48 stored is held and skipped; any other
day, including one a stopped run left short, is fetched again on the next run.
A failed day is reported and the run goes on; the exit code is 1 when any day
failed. The report lists the days still not held, failed or short.

Run in the Airflow container, which holds the gridskew_prod connection:
    python -m ingestion.elexon.demand_backfill --dry-run
    python -m ingestion.elexon.demand_backfill
"""

import argparse
import json
import sys
import time
from datetime import date, datetime, timedelta, timezone

from ingestion.database import connect_to_gridskew_prod
from ingestion.elexon import demand_poller

# Two days before the first settlement date read (22 August 2025), so the
# last forecast issued before its first half-hour is held.
FIRST_PUBLISH_DAY = date(2025, 8, 20)
# The day after the last settlement date read (16 August 2026), so the first
# outturn issued after its last half-hour is held.
LAST_PUBLISH_DAY = date(2026, 8, 17)
ISSUES_PER_PUBLISH_DAY = 48
PAUSE_SECONDS = 1
TABLES = {"NDF": "raw.elexon_ndf", "INDO": "raw.elexon_indo"}


def publish_days(first=FIRST_PUBLISH_DAY, last=LAST_PUBLISH_DAY):
    """Every UTC publish day from first to last, both included."""

    days = []
    day = first
    while day <= last:
        days.append(day)
        day += timedelta(days=1)
    return days


def publish_window(day):
    """The UTC publish window of one day: its midnight to the next."""

    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    return start, start + timedelta(days=1)


def held_days(conn, dataset, days):
    """The days among `days` stored whole for the dataset."""

    first_start, _ = publish_window(min(days))
    _, last_end = publish_window(max(days))
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT (publish_time AT TIME ZONE 'UTC')::date AS publish_day, "
            "count(DISTINCT publish_time) "
            f"FROM {TABLES[dataset]} "
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


def held_days_by_dataset(conn, days):
    held = {}
    for dataset in TABLES:
        held[dataset] = held_days(conn, dataset, days)
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


def plan(conn, days):
    """What a run would fetch, without a request."""

    held = held_days_by_dataset(conn, days)
    summary = {
        "first_day": min(days).isoformat(),
        "last_day": max(days).isoformat(),
        "days": len(days),
    }
    requests_to_make = 0
    for dataset in TABLES:
        pending = days_to_fetch(days, held[dataset])
        requests_to_make += len(pending)
        summary[dataset] = {
            "held": len(held[dataset]),
            "days_to_fetch": iso_dates(pending),
        }
    summary["requests_to_make"] = requests_to_make
    return summary


def backfill(conn, days, pause_seconds=PAUSE_SECONDS):
    """Fetch every publish day not yet held, for each dataset; return a report."""

    held_before = held_days_by_dataset(conn, days)
    outcomes = {}
    first_request = True
    for dataset in TABLES:
        outcome = {"fetched": 0, "rows": 0, "failed": {}}
        outcomes[dataset] = outcome
        for day in days_to_fetch(days, held_before[dataset]):
            if not first_request:
                time.sleep(pause_seconds)
            first_request = False
            publish_from, publish_to = publish_window(day)
            try:
                summary = demand_poller.run(conn, dataset, publish_from, publish_to)
            except Exception as error:
                conn.rollback()
                outcome["failed"][day.isoformat()] = str(error)
                print(
                    json.dumps(
                        {
                            "dataset": dataset,
                            "publish_day": day.isoformat(),
                            "failed": str(error),
                        }
                    )
                )
                continue
            outcome["fetched"] += 1
            outcome["rows"] += summary["rows"]
            print(json.dumps(summary))

    held_after = held_days_by_dataset(conn, days)
    return report(days, held_before, held_after, outcomes)


def report(days, held_before, held_after, outcomes):
    backfill_report = {
        "first_day": min(days).isoformat(),
        "last_day": max(days).isoformat(),
        "days": len(days),
    }
    for dataset in TABLES:
        outcome = outcomes[dataset]
        backfill_report[dataset] = {
            "skipped_as_held": len(held_before[dataset]),
            "fetched": outcome["fetched"],
            "rows": outcome["rows"],
            "failed": outcome["failed"],
            "not_held_after_run": iso_dates(days_to_fetch(days, held_after[dataset])),
        }
    return backfill_report


def exit_code(backfill_report):
    for dataset in TABLES:
        if backfill_report[dataset]["failed"]:
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
    days = publish_days()
    conn = connect_to_gridskew_prod()
    try:
        if arguments.dry_run:
            print(json.dumps(plan(conn, days)))
            return 0
        backfill_report = backfill(conn, days)
    finally:
        conn.close()
    print(json.dumps(backfill_report))
    return exit_code(backfill_report)


if __name__ == "__main__":
    sys.exit(main())
