import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests
import yaml

from ingestion.elexon import wind_backfill, wind_poller
from ingestion.routing import ContractRejected
from tests.fakes import cursor_of

DBT_PROJECT = Path(__file__).parent.parent / "dbt" / "dbt_project.yml"
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
