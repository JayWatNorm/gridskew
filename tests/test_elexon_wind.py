import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import psycopg
import pytest
import requests
import yaml

from ingestion.elexon import wind_backfill, wind_poller
from ingestion.routing import ContractRejected
from tests.fakes import cursor_of

DBT_PROJECT = Path(__file__).parent.parent / "dbt" / "dbt_project.yml"
ELEXON_FIXTURES = Path(__file__).parent / "fixtures" / "elexon"
WINDFOR_URL = "https://data.elexon.co.uk/bmrs/api/v1/datasets/WINDFOR"
FUELHH_URL = "https://data.elexon.co.uk/bmrs/api/v1/datasets/FUELHH"
PUBLISH_FROM = datetime(2025, 8, 22, tzinfo=timezone.utc)
PUBLISH_TO = datetime(2025, 8, 23, tzinfo=timezone.utc)
WINDFOR_COLUMNS = ("start_time", "publish_time", "generation", "retrieved_at")
FUELHH_COLUMNS = (
    "settlement_date",
    "settlement_period",
    "start_time",
    "fuel_type",
    "publish_time",
    "generation",
    "retrieved_at",
)
AUGUST_20 = date(2025, 8, 20)
AUGUST_21 = date(2025, 8, 21)
AUGUST_22 = date(2025, 8, 22)
AUGUST_23 = date(2025, 8, 23)
NOVEMBER_10 = date(2026, 11, 10)
# Every issue's span from 21:00 UK time holds the 25 October 2026 clock change.
AUTUMN_CHANGE_PUBLISH_DAY = date(2026, 10, 24)
AUTUMN_CHANGE_DATE = date(2026, 10, 25)
SPRING_CHANGE_DATE = date(2027, 3, 28)


def windfor_row(publish_time, start_time, generation):
    """A WINDFOR row shaped as the API specification and the 2025 requests show."""

    return {
        "dataset": "WINDFOR",
        "publishTime": publish_time,
        "startTime": start_time,
        "generation": generation,
    }


def fuelhh_row(settlement_date, settlement_period, start_time, generation):
    """A FUELHH WIND row, published as its period ends."""

    published = datetime.strptime(start_time, "%Y-%m-%dT%H:%M:%SZ") + timedelta(
        minutes=30
    )
    return {
        "dataset": "FUELHH",
        "publishTime": published.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "startTime": start_time,
        "settlementDate": settlement_date,
        "settlementPeriod": settlement_period,
        "fuelType": "WIND",
        "generation": generation,
    }


def two_windfor_issues():
    """Two issues of 22 August 2025, each with its first two hourly points."""

    return [
        windfor_row("2025-08-22T02:30:00Z", "2025-08-21T20:00:00Z", 3105),
        windfor_row("2025-08-22T02:30:00Z", "2025-08-21T21:00:00Z", 2987),
        windfor_row("2025-08-22T04:30:00Z", "2025-08-21T20:00:00Z", 3110),
        windfor_row("2025-08-22T04:30:00Z", "2025-08-21T21:00:00Z", 2990),
    ]


def first_two_fuelhh_periods():
    return [
        fuelhh_row("2025-08-22", 1, "2025-08-21T23:00:00Z", 2578),
        fuelhh_row("2025-08-22", 2, "2025-08-21T23:30:00Z", 2541),
    ]


def response_with(body):
    response = MagicMock()
    response.json.return_value = body
    return response


def failed_response(status, message):
    response = MagicMock()
    response.status_code = status
    response.raise_for_status.side_effect = requests.HTTPError(
        message, response=response
    )
    return response


def http_answering(*responses):
    """Stand in for the HTTP boundary: each request gets the next response."""

    return patch(
        "ingestion.elexon.wind_poller.requests.get", side_effect=list(responses)
    )


def stored_rows(conn):
    """The rows of the single insert the fake connection's cursor was given."""

    return cursor_of(conn).executemany.call_args.args[1]


def tables_written(conn):
    tables = []
    for call in cursor_of(conn).executemany.call_args_list:
        tables.append(call.args[0].split()[2])
    return tables


def requested(get):
    """Each request made, as (URL, parameters)."""

    made = []
    for call in get.call_args_list:
        made.append((call.args[0], call.kwargs["params"]))
    return made


def windfor_request(day):
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    return (
        WINDFOR_URL,
        {
            "publishDateTimeFrom": start.strftime("%Y-%m-%dT%H:%MZ"),
            "publishDateTimeTo": end.strftime("%Y-%m-%dT%H:%MZ"),
        },
    )


def fuelhh_request(settlement_date):
    return (
        FUELHH_URL,
        {
            "settlementDateFrom": settlement_date.isoformat(),
            "settlementDateTo": settlement_date.isoformat(),
            "fuelType": "WIND",
        },
    )


def connection_storing(*counts_per_query):
    """A fake connection whose held-day queries return each list in turn:
    WINDFOR then FUELHH before the run, and WINDFOR then FUELHH after it."""

    conn = MagicMock()
    cursor_of(conn).fetchall.side_effect = list(counts_per_query)
    return conn


def test_a_windfor_request_asks_for_one_utc_publish_window():
    british_summer_time = timezone(timedelta(hours=1))
    publish_from = datetime(2025, 8, 22, 1, 0, tzinfo=british_summer_time)

    with http_answering(response_with({"data": []})) as get:
        wind_poller.run_windfor(MagicMock(), publish_from, PUBLISH_TO)

    assert requested(get) == [
        (
            WINDFOR_URL,
            {
                "publishDateTimeFrom": "2025-08-22T00:00Z",
                "publishDateTimeTo": "2025-08-23T00:00Z",
            },
        )
    ]


def test_a_fuelhh_request_asks_for_one_settlement_date_of_wind_only():
    with http_answering(response_with({"data": []})) as get:
        wind_poller.run_fuelhh(MagicMock(), AUGUST_22)

    assert requested(get) == [fuelhh_request(AUGUST_22)]


def test_each_windfor_field_lands_in_its_own_column():
    conn = MagicMock()

    with http_answering(response_with({"data": two_windfor_issues()})):
        wind_poller.run_windfor(conn, PUBLISH_FROM, PUBLISH_TO)

    first_row = dict(zip(WINDFOR_COLUMNS, stored_rows(conn)[1], strict=True))
    retrieved_at = first_row.pop("retrieved_at")
    assert first_row == {
        "start_time": datetime(2025, 8, 21, 21, 0, tzinfo=timezone.utc),
        "publish_time": datetime(2025, 8, 22, 2, 30, tzinfo=timezone.utc),
        "generation": 2987,
    }
    assert retrieved_at.tzinfo == timezone.utc


def test_each_fuelhh_field_lands_in_its_own_column():
    # Period 1 of 22 August 2025 starts at 23:00 UTC on 21 August.
    conn = MagicMock()

    with http_answering(response_with({"data": first_two_fuelhh_periods()})):
        wind_poller.run_fuelhh(conn, AUGUST_22)

    first_row = dict(zip(FUELHH_COLUMNS, stored_rows(conn)[0], strict=True))
    del first_row["retrieved_at"]
    assert first_row == {
        "settlement_date": date(2025, 8, 22),
        "settlement_period": 1,
        "start_time": datetime(2025, 8, 21, 23, 0, tzinfo=timezone.utc),
        "fuel_type": "WIND",
        "publish_time": datetime(2025, 8, 21, 23, 30, tzinfo=timezone.utc),
        "generation": 2578,
    }


def test_every_row_of_a_request_shares_one_retrieval_time():
    conn = MagicMock()

    with http_answering(response_with({"data": two_windfor_issues()})):
        wind_poller.run_windfor(conn, PUBLISH_FROM, PUBLISH_TO)

    retrieval_times = set()
    for row in stored_rows(conn):
        retrieval_times.add(row[3])
    assert len(retrieval_times) == 1


@pytest.mark.parametrize(
    ("parse", "load", "rows", "conflict_key"),
    [
        pytest.param(
            wind_poller.parse_windfor,
            wind_poller.load_windfor,
            two_windfor_issues(),
            "(start_time, publish_time)",
            id="WINDFOR",
        ),
        pytest.param(
            wind_poller.parse_fuelhh,
            wind_poller.load_fuelhh,
            first_two_fuelhh_periods(),
            "(settlement_date, settlement_period, fuel_type, publish_time)",
            id="FUELHH",
        ),
    ],
)
def test_a_loader_ignores_a_row_already_stored_on_its_key(
    parse, load, rows, conflict_key
):
    conn = MagicMock()

    load(parse(rows, PUBLISH_FROM), conn)

    statement = cursor_of(conn).executemany.call_args.args[0]
    assert statement.endswith(f"ON CONFLICT {conflict_key} DO NOTHING")


def test_a_whole_window_loads_every_row_and_counts_its_issues():
    conn = MagicMock()

    with http_answering(response_with({"data": two_windfor_issues()})):
        summary = wind_poller.run_windfor(conn, PUBLISH_FROM, PUBLISH_TO)

    assert tables_written(conn) == ["raw.elexon_windfor"]
    assert len(stored_rows(conn)) == 4
    assert summary["rows"] == 4
    assert summary["issues"] == 2


def test_a_date_with_no_row_is_valid_and_writes_nothing():
    conn = MagicMock()

    with http_answering(response_with({"data": []})):
        summary = wind_poller.run_fuelhh(conn, AUGUST_22)

    assert summary["rows"] == 0
    assert summary["periods"] == 0
    conn.cursor.assert_not_called()


@pytest.mark.parametrize(
    "body",
    [
        pytest.param([], id="bare-list"),
        pytest.param({"data": None}, id="null-data"),
        pytest.param({"metadata": {}}, id="no-data"),
    ],
)
def test_a_response_without_a_data_list_fails_before_any_write(body):
    conn = MagicMock()

    with http_answering(response_with(body)):
        with pytest.raises(
            RuntimeError,
            match="Invalid response returned from Elexon FUELHH API: expected a data list",
        ):
            wind_poller.run_fuelhh(conn, AUGUST_22)

    conn.cursor.assert_not_called()


def test_a_point_without_a_value_is_quarantined_and_no_row_of_its_window_loads():
    # The API marks generation nullable.
    rows = two_windfor_issues()
    rows[2]["generation"] = None
    conn = MagicMock()

    with http_answering(response_with({"data": rows})):
        with pytest.raises(ContractRejected, match="Quarantined 1 rows"):
            wind_poller.run_windfor(conn, PUBLISH_FROM, PUBLISH_TO)

    assert tables_written(conn) == ["raw.endpoint_quarantine"]


def test_the_backfill_asks_for_362_publish_days_and_360_settlement_dates():
    days_to_hold = wind_backfill.days_by_dataset()

    windfor_days = days_to_hold["WINDFOR"]
    fuelhh_dates = days_to_hold["FUELHH"]
    assert (windfor_days[0], windfor_days[-1]) == (AUGUST_20, date(2026, 8, 16))
    assert len(set(windfor_days)) == 362
    assert (fuelhh_dates[0], fuelhh_dates[-1]) == (AUGUST_22, date(2026, 8, 16))
    assert len(set(fuelhh_dates)) == 360


def test_the_backfill_covers_the_q30_window_and_the_issues_before_it():
    # Two publish days before the window's first settlement date hold its last
    # forecast; the outturn is asked for by the window's own dates.
    variables = yaml.safe_load(DBT_PROJECT.read_text(encoding="utf-8"))["vars"]
    window_from = date.fromisoformat(variables["q30_window_from"])
    window_until = date.fromisoformat(variables["q30_window_until"])

    assert wind_backfill.FIRST_WINDFOR_PUBLISH_DAY == window_from - timedelta(days=2)
    assert wind_backfill.LAST_WINDFOR_PUBLISH_DAY == window_until
    assert wind_backfill.FIRST_FUELHH_SETTLEMENT_DATE == window_from
    assert wind_backfill.LAST_FUELHH_SETTLEMENT_DATE == window_until


def test_a_publish_day_is_held_only_with_8_distinct_issues():
    held = wind_backfill.days_with_every_issue(
        [(AUGUST_20, 8), (AUGUST_21, 7), (AUGUST_22, 9)]
    )

    assert held == {AUGUST_20, AUGUST_22}


def test_a_settlement_date_is_held_only_with_exactly_its_calendar_periods():
    spring_change = date(2026, 3, 29)
    autumn_change = date(2025, 10, 26)
    held = wind_backfill.dates_with_every_period(
        [
            (AUGUST_22, 48),
            (AUGUST_23, 47),
            (spring_change, 46),
            (autumn_change, 48),
            (date(2025, 10, 27), 49),
        ]
    )

    assert held == {AUGUST_22, spring_change}


def test_held_days_are_skipped_and_the_others_fetched_windfor_first():
    conn = connection_storing([(AUGUST_21, 8)], [(AUGUST_22, 48)], [], [])
    days_to_hold = {
        "WINDFOR": [AUGUST_20, AUGUST_21],
        "FUELHH": [AUGUST_22, AUGUST_23],
    }

    with http_answering(
        response_with({"data": []}), response_with({"data": []})
    ) as get:
        report = wind_backfill.backfill(conn, days_to_hold, pause_seconds=0)

    assert requested(get) == [windfor_request(AUGUST_20), fuelhh_request(AUGUST_23)]
    assert report["WINDFOR"]["skipped_as_held"] == 1
    assert report["WINDFOR"]["requested"] == ["2025-08-20"]
    assert report["FUELHH"]["skipped_as_held"] == 1
    assert report["FUELHH"]["requested"] == ["2025-08-23"]


@pytest.mark.parametrize(
    ("first_answer", "failure"),
    [
        pytest.param(
            failed_response(429, "429 Too Many Requests"),
            "rate limited (HTTP 429): 429 Too Many Requests",
            id="rate-limited",
        ),
        pytest.param(
            failed_response(500, "500 Internal Server Error"),
            "server error (HTTP 500): 500 Internal Server Error",
            id="server-error",
        ),
        pytest.param(
            failed_response(404, "404 Not Found"),
            "request refused (HTTP 404): 404 Not Found",
            id="other-refusal",
        ),
        pytest.param(
            requests.ConnectionError("connection reset"),
            "other failure: connection reset",
            id="no-response",
        ),
    ],
)
def test_a_failed_request_stops_the_run_before_any_further_request(
    first_answer, failure
):
    conn = connection_storing([], [], [], [])
    days_to_hold = {"WINDFOR": [AUGUST_20, AUGUST_21], "FUELHH": [AUGUST_22]}

    with http_answering(
        first_answer,
        response_with({"data": two_windfor_issues()}),
        response_with({"data": first_two_fuelhh_periods()}),
    ) as get:
        report = wind_backfill.backfill(conn, days_to_hold, pause_seconds=0)

    assert requested(get) == [windfor_request(AUGUST_20)]
    assert report["WINDFOR"]["requested"] == ["2025-08-20"]
    assert report["WINDFOR"]["failed"] == {"2025-08-20": failure}
    assert report["WINDFOR"]["not_held_after_run"] == ["2025-08-20", "2025-08-21"]
    assert report["FUELHH"]["requested"] == []
    assert report["FUELHH"]["not_held_after_run"] == ["2025-08-22"]
    assert wind_backfill.exit_code(report) == 1


def test_a_quarantined_row_stops_the_run_and_names_the_dataset_and_day(capsys):
    rows_with_a_missing_value = two_windfor_issues()
    rows_with_a_missing_value[2]["generation"] = None
    conn = connection_storing([], [], [(AUGUST_20, 8)], [])
    days_to_hold = {"WINDFOR": [AUGUST_20, AUGUST_21], "FUELHH": [AUGUST_22]}

    with http_answering(
        response_with({"data": two_windfor_issues()}),
        response_with({"data": rows_with_a_missing_value}),
        response_with({"data": first_two_fuelhh_periods()}),
    ) as get:
        report = wind_backfill.backfill(conn, days_to_hold, pause_seconds=0)

    stop_notice = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert requested(get) == [windfor_request(AUGUST_20), windfor_request(AUGUST_21)]
    assert stop_notice["stopped"] == (
        "Back-fill stopped at WINDFOR 2025-08-21: quarantined rows. "
        "No further request was made."
    )
    assert report["WINDFOR"]["failed"] == {
        "2025-08-21": "quarantined rows: Quarantined 1 rows due to validation errors"
    }
    assert report["WINDFOR"]["held_after_run"] == 1
    assert report["WINDFOR"]["not_held_after_run"] == ["2025-08-21"]
    assert report["FUELHH"]["requested"] == []


def test_a_date_still_short_after_its_fetch_is_listed_but_is_not_a_failure():
    conn = connection_storing([(AUGUST_20, 8)], [], [(AUGUST_20, 8)], [(AUGUST_22, 2)])
    days_to_hold = {"WINDFOR": [AUGUST_20], "FUELHH": [AUGUST_22]}

    with http_answering(response_with({"data": first_two_fuelhh_periods()})):
        report = wind_backfill.backfill(conn, days_to_hold, pause_seconds=0)

    assert report["FUELHH"]["requested"] == ["2025-08-22"]
    assert report["FUELHH"]["failed"] == {}
    assert report["FUELHH"]["not_held_after_run"] == ["2025-08-22"]
    assert wind_backfill.exit_code(report) == 0


def test_a_dry_run_makes_no_request_and_lists_the_days_to_fetch():
    conn = connection_storing([(AUGUST_21, 8)], [(AUGUST_22, 47)])
    days_to_hold = {"WINDFOR": [AUGUST_20, AUGUST_21], "FUELHH": [AUGUST_22]}

    with http_answering() as get:
        plan = wind_backfill.plan(conn, days_to_hold)

    get.assert_not_called()
    assert plan == {
        "WINDFOR": {
            "first_day": "2025-08-20",
            "last_day": "2025-08-21",
            "days": 2,
            "held": 1,
            "days_to_fetch": ["2025-08-20"],
        },
        "FUELHH": {
            "first_day": "2025-08-22",
            "last_day": "2025-08-22",
            "days": 1,
            "held": 0,
            "days_to_fetch": ["2025-08-22"],
        },
        "requests_to_make": 2,
    }


def recorded(file_name):
    """A recorded response body from tests/fixtures/elexon."""

    return json.loads((ELEXON_FIXTURES / file_name).read_text(encoding="utf-8"))


def stored_issues(publish_day, issue_points):
    """Rows as the WINDFOR completeness query returns them: one per stored
    issue of the publish day, with its publish time and its point count."""

    first_issue = datetime(
        publish_day.year, publish_day.month, publish_day.day, 2, 30, tzinfo=timezone.utc
    )
    rows = []
    for issue_number, points in enumerate(issue_points):
        published = first_issue + timedelta(hours=2 * issue_number)
        rows.append((publish_day, published, points))
    return rows


def stored_periods(settlement_date, periods):
    """Rows as the FUELHH completeness query returns them: one per stored
    publication of a period."""

    rows = []
    for period in periods:
        rows.append((settlement_date, period))
    return rows


def periods_from(first, last):
    return list(range(first, last + 1))


def recorded_autumn_change_periods():
    rows = []
    for row in recorded("fuelhh_wind_autumn_change.json")["data"]:
        rows.append(
            (date.fromisoformat(row["settlementDate"]), row["settlementPeriod"])
        )
    return rows


def days_newest_first(newest, oldest):
    days = []
    day = newest
    while day >= oldest:
        days.append(day)
        day -= timedelta(days=1)
    return days


@pytest.mark.parametrize(
    ("publish_day", "points"),
    [
        pytest.param(NOVEMBER_10, 73, id="winter"),
        pytest.param(date(2026, 10, 22), 73, id="autumn-change-after-span"),
        pytest.param(date(2026, 10, 23), 74, id="autumn-change-at-span-end"),
        pytest.param(AUTUMN_CHANGE_PUBLISH_DAY, 74, id="autumn-change-mid-span"),
        pytest.param(AUTUMN_CHANGE_DATE, 74, id="autumn-change-at-span-start"),
        pytest.param(date(2026, 10, 26), 73, id="autumn-change-before-span"),
        pytest.param(date(2027, 3, 25), 73, id="spring-change-after-span"),
        pytest.param(date(2027, 3, 26), 72, id="spring-change-at-span-end"),
        pytest.param(date(2027, 3, 27), 72, id="spring-change-mid-span"),
        pytest.param(SPRING_CHANGE_DATE, 72, id="spring-change-at-span-start"),
        pytest.param(date(2027, 3, 29), 73, id="spring-change-before-span"),
        pytest.param(date(2026, 3, 28), 72, id="recorded-spring-day"),
    ],
)
def test_an_issue_holds_a_point_each_hour_from_21_00_uk_time_the_day_before_to_21_00_two_days_after(
    publish_day, points
):
    assert wind_poller.expected_points_per_issue(publish_day) == points


@pytest.mark.parametrize(
    ("file_name", "publish_day", "first_and_last_issue"),
    [
        pytest.param(
            "windfor_autumn_change.json",
            AUTUMN_CHANGE_PUBLISH_DAY,
            ("2026-10-24T03:30:00Z", "2026-10-24T23:30:00Z"),
            id="autumn",
        ),
        pytest.param(
            "windfor_spring_change.json",
            date(2026, 3, 28),
            ("2026-03-28T02:30:00Z", "2026-03-28T22:30:00Z"),
            id="spring",
        ),
    ],
)
def test_each_recorded_issue_of_a_clock_change_day_holds_its_expected_points(
    file_name, publish_day, first_and_last_issue
):
    points_by_issue = {}
    for row in recorded(file_name)["data"]:
        issue = row["publishTime"]
        points_by_issue[issue] = points_by_issue.get(issue, 0) + 1

    expected_points = wind_poller.expected_points_per_issue(publish_day)
    first_issue, last_issue = first_and_last_issue
    assert points_by_issue == {
        first_issue: expected_points,
        last_issue: expected_points,
    }


@pytest.mark.parametrize(
    ("stored", "complete"),
    [
        pytest.param(stored_issues(NOVEMBER_10, [73] * 8), {NOVEMBER_10}, id="8-of-73"),
        pytest.param(stored_issues(NOVEMBER_10, [73] * 9), {NOVEMBER_10}, id="9-of-73"),
        pytest.param(
            stored_issues(AUTUMN_CHANGE_PUBLISH_DAY, [74] * 8),
            {AUTUMN_CHANGE_PUBLISH_DAY},
            id="8-of-74-on-a-74-point-day",
        ),
        pytest.param(stored_issues(NOVEMBER_10, [73] * 7), set(), id="7-issues"),
        pytest.param(
            stored_issues(NOVEMBER_10, [73] * 7 + [72]), set(), id="last-issue-short"
        ),
        pytest.param(
            stored_issues(NOVEMBER_10, [73] * 8 + [40]), set(), id="partial-ninth"
        ),
        pytest.param(
            stored_issues(AUTUMN_CHANGE_PUBLISH_DAY, [73] * 8),
            set(),
            id="8-of-73-on-a-74-point-day",
        ),
    ],
)
def test_a_publish_day_is_complete_with_8_issues_each_holding_its_expected_points(
    stored, complete
):
    assert wind_poller.windfor_days_complete(stored) == complete


@pytest.mark.parametrize(
    ("stored", "complete"),
    [
        pytest.param(
            stored_periods(NOVEMBER_10, periods_from(1, 48)), {NOVEMBER_10}, id="48"
        ),
        pytest.param(
            recorded_autumn_change_periods(), {AUTUMN_CHANGE_DATE}, id="recorded-50"
        ),
        pytest.param(
            stored_periods(SPRING_CHANGE_DATE, periods_from(1, 46)),
            {SPRING_CHANGE_DATE},
            id="46",
        ),
        pytest.param(
            stored_periods(NOVEMBER_10, periods_from(1, 48) + [7]),
            {NOVEMBER_10},
            id="period-7-published-twice",
        ),
        pytest.param(
            stored_periods(NOVEMBER_10, periods_from(1, 47)), set(), id="1-to-47"
        ),
        pytest.param(
            stored_periods(NOVEMBER_10, periods_from(2, 49)), set(), id="2-to-49"
        ),
        pytest.param(
            stored_periods(NOVEMBER_10, periods_from(1, 47) + [49]),
            set(),
            id="1-to-47-and-49",
        ),
        pytest.param(
            stored_periods(NOVEMBER_10, [0] + periods_from(2, 48)),
            set(),
            id="0-and-2-to-48",
        ),
        pytest.param(
            stored_periods(AUTUMN_CHANGE_DATE, periods_from(1, 48)),
            set(),
            id="48-on-a-50-period-date",
        ),
        pytest.param(
            stored_periods(SPRING_CHANGE_DATE, periods_from(1, 48)),
            set(),
            id="48-on-a-46-period-date",
        ),
    ],
)
def test_a_settlement_date_is_complete_with_exactly_periods_1_to_its_calendar_count(
    stored, complete
):
    assert wind_poller.fuelhh_dates_complete(stored) == complete


@pytest.mark.parametrize(
    ("target", "look_back"),
    [
        pytest.param(date(2026, 11, 1), [], id="first-capture-day"),
        pytest.param(
            date(2026, 11, 3),
            [date(2026, 11, 2), date(2026, 11, 1)],
            id="down-to-the-first-capture-day",
        ),
        pytest.param(
            date(2026, 11, 9),
            days_newest_first(date(2026, 11, 8), date(2026, 11, 2)),
            id="seven-days",
        ),
        pytest.param(
            date(2026, 12, 1),
            days_newest_first(date(2026, 11, 30), date(2026, 11, 24)),
            id="across-a-month",
        ),
    ],
)
def test_the_look_back_holds_the_seven_days_before_the_target_newest_first(
    target, look_back
):
    assert wind_poller.look_back_days(target) == look_back


def test_a_day_complete_or_held_in_quarantine_is_not_asked():
    days = days_newest_first(date(2026, 11, 30), date(2026, 11, 24))

    asked = wind_poller.days_to_ask(
        days,
        complete_days={date(2026, 11, 30), date(2026, 11, 26)},
        held_days={date(2026, 11, 28)},
    )

    assert asked == [
        date(2026, 11, 29),
        date(2026, 11, 27),
        date(2026, 11, 25),
        date(2026, 11, 24),
    ]


NOVEMBER_5 = date(2026, 11, 5)
DECEMBER_1 = date(2026, 12, 1)
# 24 to 30 November 2026 except 27 November.
LATE_NOVEMBER_COMPLETE_DAYS = [
    date(2026, 11, 30),
    date(2026, 11, 29),
    date(2026, 11, 28),
    date(2026, 11, 26),
    date(2026, 11, 25),
    date(2026, 11, 24),
]


def whole_windfor_days(publish_days):
    """Completeness rows of 8 issues of 73 points for each day."""

    rows = []
    for publish_day in publish_days:
        rows.extend(stored_issues(publish_day, [73] * 8))
    return rows


def whole_fuelhh_dates(settlement_dates):
    """Completeness rows of periods 1 to 48 for each date."""

    rows = []
    for settlement_date in settlement_dates:
        rows.extend(stored_periods(settlement_date, periods_from(1, 48)))
    return rows


def windfor_quarantine_row(publish_day):
    """A quarantine query row: publishDateTimeFrom as run_windfor stores it."""

    return (f"{publish_day.isoformat()}T00:00:00+00:00",)


def fuelhh_quarantine_row(settlement_date):
    """A quarantine query row: settlementDate as run_fuelhh stores it."""

    return (settlement_date.isoformat(),)


def database_holding(stored=(), stored_after=None, quarantined=()):
    """A fake connection for the capture functions. The completeness query
    returns `stored` before the requests and `stored_after` (by default the
    same rows) after them; the quarantine query returns `quarantined`."""

    if stored_after is None:
        stored_after = stored
    completeness_answers = [list(stored), list(stored_after)]
    conn = MagicMock()
    cursor = cursor_of(conn)

    def answer(query, params):
        if "raw.endpoint_quarantine" in query:
            cursor.fetchall.return_value = list(quarantined)
            return
        cursor.fetchall.return_value = completeness_answers.pop(0)

    cursor.execute.side_effect = answer
    return conn


def no_pause():
    """Stand in for the second between look-back requests."""

    return patch("ingestion.elexon.wind_poller.time.sleep")


def windfor_requests(publish_days):
    made = []
    for publish_day in publish_days:
        made.append(windfor_request(publish_day))
    return made


def empty_reply():
    return response_with({"data": []})


def whole_windfor_reply():
    return response_with(recorded("windfor_autumn_change.json"))


def capture_summary(caplog):
    """The summary a capture task logged, as a dictionary."""

    for record in caplog.records:
        logged = json.loads(record.getMessage())
        if "task" in logged:
            return logged
    raise AssertionError("no capture summary was logged")


WINDFOR_CAPTURE = SimpleNamespace(
    target=wind_poller.capture_windfor_target,
    look_back=wind_poller.capture_windfor_look_back,
    request=windfor_request,
    whole_days=whole_windfor_days,
    quarantine_row=windfor_quarantine_row,
    reply="windfor_autumn_change.json",
)
FUELHH_CAPTURE = SimpleNamespace(
    target=wind_poller.capture_fuelhh_target,
    look_back=wind_poller.capture_fuelhh_look_back,
    request=fuelhh_request,
    whole_days=whole_fuelhh_dates,
    quarantine_row=fuelhh_quarantine_row,
    reply="fuelhh_wind_autumn_change.json",
)
CAPTURES = [
    pytest.param(WINDFOR_CAPTURE, id="WINDFOR"),
    pytest.param(FUELHH_CAPTURE, id="FUELHH"),
]


@pytest.mark.parametrize("capture", CAPTURES)
def test_the_target_task_asks_for_its_target_alone(capture):
    conn = database_holding(stored=capture.whole_days(LATE_NOVEMBER_COMPLETE_DAYS))

    with http_answering(response_with(recorded(capture.reply))) as get:
        capture.target(conn, DECEMBER_1)

    assert requested(get) == [capture.request(DECEMBER_1)]


@pytest.mark.parametrize("capture", CAPTURES)
def test_the_look_back_asks_only_the_days_before_the_target_not_complete(capture):
    conn = database_holding(stored=capture.whole_days(LATE_NOVEMBER_COMPLETE_DAYS))

    with http_answering(response_with(recorded(capture.reply))) as get:
        capture.look_back(conn, DECEMBER_1)

    assert requested(get) == [capture.request(date(2026, 11, 27))]


def test_a_complete_target_is_not_asked_again():
    conn = database_holding(stored=whole_windfor_days([NOVEMBER_10]))

    with http_answering() as get:
        wind_poller.capture_windfor_target(conn, NOVEMBER_10)

    get.assert_not_called()


@pytest.mark.parametrize("capture", CAPTURES)
def test_a_target_held_in_quarantine_fails_the_task_without_a_request(capture):
    conn = database_holding(quarantined=[capture.quarantine_row(NOVEMBER_5)])

    with http_answering() as get:
        with pytest.raises(ContractRejected, match="2026-11-05"):
            capture.target(conn, NOVEMBER_5)

    get.assert_not_called()


def test_the_look_back_asks_every_day_but_the_held_one_then_fails_without_a_retry():
    conn = database_holding(quarantined=[windfor_quarantine_row(NOVEMBER_5)])

    with no_pause(), http_answering(*[empty_reply()] * 6) as get:
        with pytest.raises(ContractRejected, match="2026-11-05"):
            wind_poller.capture_windfor_look_back(conn, date(2026, 11, 9))

    assert requested(get) == windfor_requests(
        [
            date(2026, 11, 8),
            date(2026, 11, 7),
            date(2026, 11, 6),
            date(2026, 11, 4),
            date(2026, 11, 3),
            date(2026, 11, 2),
        ]
    )


def test_a_quarantined_day_is_followed_by_the_next_days_request_then_the_task_fails():
    reply_with_a_missing_value = recorded("windfor_autumn_change.json")
    reply_with_a_missing_value["data"][5]["generation"] = None
    conn = database_holding()

    with (
        no_pause(),
        http_answering(response_with(reply_with_a_missing_value), empty_reply()) as get,
    ):
        with pytest.raises(ContractRejected, match="2026-11-02"):
            wind_poller.capture_windfor_look_back(conn, date(2026, 11, 3))

    assert tables_written(conn) == ["raw.endpoint_quarantine"]
    assert requested(get) == windfor_requests([date(2026, 11, 2), date(2026, 11, 1)])


def test_the_look_back_asks_each_day_once_past_failed_requests_then_fails():
    conn = database_holding()

    with (
        no_pause(),
        http_answering(
            failed_response(503, "503 Service Unavailable"),
            requests.ConnectionError("connection reset"),
            whole_windfor_reply(),
        ) as get,
    ):
        with pytest.raises(
            RuntimeError, match="requests failed.*2026-11-03.*2026-11-02"
        ):
            wind_poller.capture_windfor_look_back(conn, date(2026, 11, 4))

    assert requested(get) == windfor_requests(
        [date(2026, 11, 3), date(2026, 11, 2), date(2026, 11, 1)]
    )


def test_the_look_back_stops_at_once_on_http_429():
    conn = database_holding()

    with (
        no_pause(),
        http_answering(
            failed_response(429, "429 Too Many Requests"), whole_windfor_reply()
        ) as get,
    ):
        with pytest.raises(RuntimeError, match="HTTP 429"):
            wind_poller.capture_windfor_look_back(conn, date(2026, 11, 4))

    assert requested(get) == windfor_requests([date(2026, 11, 3)])


def test_the_look_back_stops_at_once_on_a_database_error():
    conn = database_holding()
    cursor_of(conn).executemany.side_effect = psycopg.OperationalError(
        "server closed the connection unexpectedly"
    )

    with (
        no_pause(),
        http_answering(whole_windfor_reply(), whole_windfor_reply()) as get,
    ):
        with pytest.raises(RuntimeError, match="database error"):
            wind_poller.capture_windfor_look_back(conn, date(2026, 11, 4))

    assert requested(get) == windfor_requests([date(2026, 11, 3)])


def test_look_back_requests_are_a_second_apart():
    conn = database_holding()

    with no_pause() as pause, http_answering(*[empty_reply()] * 3):
        wind_poller.capture_windfor_look_back(conn, date(2026, 11, 4))

    seconds_paused = []
    for pause_call in pause.call_args_list:
        seconds_paused.append(pause_call.args[0])
    assert seconds_paused == [1, 1]


def test_a_short_target_reply_ends_the_target_task_without_an_error(caplog):
    caplog.set_level(logging.INFO, logger="ingestion.elexon.wind_poller")
    conn = database_holding()

    with http_answering(empty_reply()):
        wind_poller.capture_fuelhh_target(conn, NOVEMBER_10)

    assert capture_summary(caplog)["not_complete"] == ["2026-11-10"]


def test_a_target_retry_asks_for_the_target_alone_again():
    with http_answering(
        failed_response(503, "503 Service Unavailable"), whole_windfor_reply()
    ) as get:
        with pytest.raises(RuntimeError, match="HTTP 503") as first_try:
            wind_poller.capture_windfor_target(database_holding(), NOVEMBER_10)
        wind_poller.capture_windfor_target(database_holding(), NOVEMBER_10)

    # Any failure but a contract rejection is left for Airflow to retry.
    assert not isinstance(first_try.value, ContractRejected)
    assert requested(get) == windfor_requests([NOVEMBER_10, NOVEMBER_10])


def test_a_second_look_back_run_asks_again_for_the_short_and_failed_days_only():
    first_run = database_holding(
        stored=whole_windfor_days(
            [date(2026, 11, 6), date(2026, 11, 5), date(2026, 11, 4), date(2026, 11, 3)]
        )
    )
    with (
        no_pause(),
        http_answering(
            empty_reply(),
            failed_response(503, "503 Service Unavailable"),
            whole_windfor_reply(),
        ) as first_run_get,
    ):
        with pytest.raises(RuntimeError, match="2026-11-08"):
            wind_poller.capture_windfor_look_back(first_run, date(2026, 11, 10))

    # The first run stored 7 November and its target, 10 November.
    second_run = database_holding(
        stored=whole_windfor_days(
            [
                date(2026, 11, 10),
                date(2026, 11, 7),
                date(2026, 11, 6),
                date(2026, 11, 5),
                date(2026, 11, 4),
            ]
        )
    )
    with (
        no_pause(),
        http_answering(whole_windfor_reply(), whole_windfor_reply()) as second_run_get,
    ):
        wind_poller.capture_windfor_look_back(second_run, date(2026, 11, 11))

    assert requested(first_run_get) == windfor_requests(
        [date(2026, 11, 9), date(2026, 11, 8), date(2026, 11, 7)]
    )
    assert requested(second_run_get) == windfor_requests(
        [date(2026, 11, 9), date(2026, 11, 8)]
    )


def test_the_summary_counts_each_day_and_warns_of_a_day_leaving_not_complete(caplog):
    caplog.set_level(logging.INFO, logger="ingestion.elexon.wind_poller")
    stored_before = whole_windfor_days(
        [
            date(2026, 11, 7),
            date(2026, 11, 6),
            date(2026, 11, 5),
            date(2026, 11, 4),
            date(2026, 11, 3),
        ]
    )
    stored_after = stored_before + whole_windfor_days([date(2026, 11, 8)])
    conn = database_holding(stored=stored_before, stored_after=stored_after)

    with no_pause(), http_answering(whole_windfor_reply(), empty_reply()):
        wind_poller.capture_windfor_look_back(conn, date(2026, 11, 9))

    assert capture_summary(caplog) == {
        "dataset": "WINDFOR",
        "task": "look_back",
        "target": "2026-11-09",
        "first_day": "2026-11-02",
        "last_day": "2026-11-08",
        "asked": ["2026-11-08", "2026-11-02"],
        "rows_returned": {"2026-11-08": 148, "2026-11-02": 0},
        "failed": {},
        "quarantined": [],
        "held": [],
        "not_complete": ["2026-11-02"],
        "leaving_not_complete": "2026-11-02",
    }
    warnings = []
    for record in caplog.records:
        if record.levelno == logging.WARNING:
            warnings.append(json.loads(record.getMessage())["day"])
    assert warnings == ["2026-11-02"]


def test_a_target_before_the_first_capture_day_asks_nothing():
    conn = MagicMock()

    with http_answering() as get:
        wind_poller.capture_fuelhh_target(conn, date(2026, 10, 31))
        wind_poller.capture_fuelhh_look_back(conn, date(2026, 10, 31))

    get.assert_not_called()
    conn.cursor.assert_not_called()
