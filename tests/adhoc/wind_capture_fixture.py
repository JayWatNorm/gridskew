"""Record the three wind responses the daily capture's tests replay.

Makes exactly three requests, one at a time, a second apart:

1. WINDFOR, publish day 24 October 2026: every issue's span holds the
   25 October 2026 clock change.
2. WINDFOR, publish day 28 March 2026: every issue's span holds the
   29 March 2026 clock change.
3. FUELHH, fuel type WIND, settlement date 25 October 2026: 50 periods.

Each full response is first saved verbatim in the folder named on the command
line, outside the repository. The fixtures keep the first and the last
stamped issue of each WINDFOR day, every point, and the FUELHH date whole,
rows verbatim. For each response it prints the request, its time and size,
and every issue's stamp with its point count and first and last startTime:
the evidence that settles expected_points_per_issue.

The fixtures are frozen evidence: run this only to make new ones on purpose.
From the repository root:
    python -m tests.adhoc.wind_capture_fixture <folder for the full responses>
"""

import json
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

from ingestion.elexon.wind_poller import BASE_URL, WIND_FUEL_TYPE
from ingestion.source_api import USER_AGENT, utc_text_to_the_minute

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "elexon"
AUTUMN_PUBLISH_DAY = date(2026, 10, 24)
SPRING_PUBLISH_DAY = date(2026, 3, 28)
AUTUMN_SETTLEMENT_DATE = date(2026, 10, 25)
PAUSE_SECONDS = 1


def windfor_request(publish_day):
    """The URL and parameters of one UTC publish day of WINDFOR."""

    publish_from = datetime(
        publish_day.year, publish_day.month, publish_day.day, tzinfo=timezone.utc
    )
    publish_to = publish_from + timedelta(days=1)
    params = {
        "publishDateTimeFrom": utc_text_to_the_minute(publish_from),
        "publishDateTimeTo": utc_text_to_the_minute(publish_to),
    }
    return f"{BASE_URL}/WINDFOR", params


def fuelhh_request(settlement_date):
    """The URL and parameters of one settlement date of FUELHH wind."""

    params = {
        "settlementDateFrom": settlement_date.isoformat(),
        "settlementDateTo": settlement_date.isoformat(),
        "fuelType": WIND_FUEL_TYPE,
    }
    return f"{BASE_URL}/FUELHH", params


def fetch_and_save(url, params, raw_path):
    """Make one request, save its body verbatim, then fail on an HTTP error.

    Returns the decoded body.
    """

    requested_at = datetime.now(timezone.utc)
    response = requests.get(
        url, params=params, headers={"User-Agent": USER_AGENT}, timeout=60
    )
    raw_path.write_bytes(response.content)
    print(
        f"request {response.url} at {requested_at.isoformat(timespec='seconds')}: "
        f"HTTP {response.status_code}, {len(response.content)} bytes, "
        f"saved to {raw_path}"
    )
    response.raise_for_status()
    return response.json()


def issue_summaries(rows):
    """Each issue's stamp, point count and first and last startTime, in stamp
    order."""

    start_times_by_issue = {}
    for row in rows:
        publish_time = row["publishTime"]
        if publish_time not in start_times_by_issue:
            start_times_by_issue[publish_time] = []
        start_times_by_issue[publish_time].append(row["startTime"])

    summaries = []
    for publish_time in sorted(start_times_by_issue):
        start_times = start_times_by_issue[publish_time]
        summaries.append(
            {
                "publish_time": publish_time,
                "points": len(start_times),
                "first_start": min(start_times),
                "last_start": max(start_times),
            }
        )
    return summaries


def rows_of_issues(rows, publish_times):
    """The rows of the given issues, in the order the response holds them."""

    kept = []
    for row in rows:
        if row["publishTime"] in publish_times:
            kept.append(row)
    return kept


def field_names(rows):
    names = set()
    for row in rows:
        names.update(row)
    return sorted(names)


def print_windfor_evidence(publish_day, rows, size):
    print(f"WINDFOR publish day {publish_day.isoformat()}")
    print(f"rows {len(rows)}, bytes {size}")
    print(f"fields: {field_names(rows)}")
    for summary in issue_summaries(rows):
        print(
            f"publishTime {summary['publish_time']}  points {summary['points']}  "
            f"first startTime {summary['first_start']}  last {summary['last_start']}"
        )


def print_fuelhh_evidence(settlement_date, rows, size):
    periods = []
    publish_times = []
    for row in rows:
        periods.append(row["settlementPeriod"])
        publish_times.append(row["publishTime"])
    print(f"FUELHH WIND settlement date {settlement_date.isoformat()}")
    print(f"rows {len(rows)}, bytes {size}")
    print(f"fields: {field_names(rows)}")
    print(
        f"periods: {len(set(periods))} distinct, smallest {min(periods)}, "
        f"largest {max(periods)}"
    )
    print(f"publishTime: first {min(publish_times)}, last {max(publish_times)}")


def write_fixture(file_name, rows):
    """Write rows in the endpoint's {"data": [...]} envelope, with LF endings."""

    text = json.dumps({"data": rows}, indent=2) + "\n"
    with open(FIXTURES_DIR / file_name, "w", encoding="utf-8", newline="\n") as file:
        file.write(text)
    print(f"wrote {len(rows)} rows to {FIXTURES_DIR / file_name}")


def record_windfor_day(publish_day, raw_dir, file_name):
    url, params = windfor_request(publish_day)
    raw_path = raw_dir / f"windfor-{publish_day.isoformat()}.json"
    rows = fetch_and_save(url, params, raw_path)["data"]
    print_windfor_evidence(publish_day, rows, raw_path.stat().st_size)
    summaries = issue_summaries(rows)
    first_and_last = {summaries[0]["publish_time"], summaries[-1]["publish_time"]}
    write_fixture(file_name, rows_of_issues(rows, first_and_last))


def record_fuelhh_date(settlement_date, raw_dir, file_name):
    url, params = fuelhh_request(settlement_date)
    raw_path = raw_dir / f"fuelhh-wind-{settlement_date.isoformat()}.json"
    rows = fetch_and_save(url, params, raw_path)["data"]
    print_fuelhh_evidence(settlement_date, rows, raw_path.stat().st_size)
    write_fixture(file_name, rows)


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    if len(argv) != 1:
        raise SystemExit(__doc__)
    raw_dir = Path(argv[0])
    raw_dir.mkdir(parents=True, exist_ok=True)

    record_windfor_day(AUTUMN_PUBLISH_DAY, raw_dir, "windfor_autumn_change.json")
    print()
    time.sleep(PAUSE_SECONDS)
    record_windfor_day(SPRING_PUBLISH_DAY, raw_dir, "windfor_spring_change.json")
    print()
    time.sleep(PAUSE_SECONDS)
    record_fuelhh_date(
        AUTUMN_SETTLEMENT_DATE, raw_dir, "fuelhh_wind_autumn_change.json"
    )


if __name__ == "__main__":
    main()
