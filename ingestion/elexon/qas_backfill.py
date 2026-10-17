"""Capture QAS once for every settlement date before the DAG's first run.

A bounded one-off run, outside the DAG, with the DAG's own poller: one
settlement date at a time (two requests on a 50-period date). A date already in
raw.elexon_qas is skipped, so running it again repeats only the dates still
missing. A failed date is reported and the run goes on; the exit code is 1
when any date failed.

Run in the Airflow container, which holds the gridskew_prod connection:
    python -m ingestion.elexon.qas_backfill --dry-run
    python -m ingestion.elexon.qas_backfill
"""

import argparse
import json
import sys
import time
from datetime import date, timedelta

from ingestion.elexon import qas_poller

# The first settlement date of the shortfall table.
FIRST_QAS_DATE = date(2025, 8, 22)
# The day before the DAG's first 14-day target (its start date, 20 October
# 2026, minus 14 days); tests/test_elexon_qas_dag.py ties the two together.
BACKFILL_LAST_DATE = date(2026, 10, 5)
PAUSE_SECONDS = 1


def backfill_dates(first=FIRST_QAS_DATE, last=BACKFILL_LAST_DATE):
    """Every settlement date from first to last, both included."""

    dates = []
    day = first
    while day <= last:
        dates.append(day)
        day += timedelta(days=1)
    return dates


def captured_dates(conn, dates):
    """The dates among `dates` with any row in raw.elexon_qas."""

    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT DISTINCT settlement_date FROM raw.elexon_qas "
            "WHERE settlement_date BETWEEN %s AND %s",
            (min(dates), max(dates)),
        )
        stored = set()
        for (settlement_date,) in cursor.fetchall():
            stored.add(settlement_date)
    conn.rollback()
    return stored & set(dates)


def plan(conn, dates):
    """What a run would do, without a request."""

    already_captured = captured_dates(conn, dates)
    return {
        "first_date": min(dates).isoformat(),
        "last_date": max(dates).isoformat(),
        "dates": len(dates),
        "already_captured": len(already_captured),
        "requests_to_make": len(dates) - len(already_captured),
    }


def backfill(conn, dates, pause_seconds=PAUSE_SECONDS):
    """Capture each date not yet stored; return what happened."""

    already_captured = captured_dates(conn, dates)
    summaries = []
    failed = {}
    first_request = True
    for settlement_date in dates:
        if settlement_date in already_captured:
            continue
        if not first_request:
            time.sleep(pause_seconds)
        first_request = False
        try:
            summary = qas_poller.run(conn, settlement_date)
        except Exception as error:
            conn.rollback()
            failed[settlement_date.isoformat()] = str(error)
            print(
                json.dumps(
                    {
                        "settlement_date": settlement_date.isoformat(),
                        "failed": str(error),
                    }
                )
            )
            continue
        summaries.append(summary)
        print(json.dumps(summary))
    return report(summaries, failed, len(already_captured))


def report(summaries, failed, skipped_as_captured):
    total_rows = 0
    smallest_day = None
    fewest_in_a_period = None
    for summary in summaries:
        total_rows += summary["rows"]
        if smallest_day is None or summary["rows"] < smallest_day["rows"]:
            smallest_day = {
                "settlement_date": summary["settlement_date"],
                "rows": summary["rows"],
            }
        fewest = summary["fewest_rows_in_a_period"]
        if fewest_in_a_period is None or fewest < fewest_in_a_period["rows"]:
            fewest_in_a_period = {
                "settlement_date": summary["settlement_date"],
                "rows": fewest,
            }
    return {
        "captured": len(summaries),
        "skipped_as_captured": skipped_as_captured,
        "failed": failed,
        "total_rows": total_rows,
        "smallest_day": smallest_day,
        "fewest_rows_in_a_period": fewest_in_a_period,
    }


def exit_code(backfill_report):
    if backfill_report["failed"]:
        return 1
    return 0


def airflow_connection():
    """The gridskew_prod connection, as the DAGs open it."""

    from airflow.hooks.base import BaseHook

    from ingestion.database import connect

    return connect(BaseHook.get_connection("gridskew_prod"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the dates and how many are already captured; no request",
    )
    arguments = parser.parse_args(argv)
    dates = backfill_dates()
    conn = airflow_connection()
    try:
        if arguments.dry_run:
            print(json.dumps(plan(conn, dates)))
            return 0
        backfill_report = backfill(conn, dates)
    finally:
        conn.close()
    print(json.dumps(backfill_report))
    return exit_code(backfill_report)


if __name__ == "__main__":
    sys.exit(main())
