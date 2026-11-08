"""Guard which REMIT windows the daily re-read asks for, what it stores and
when it stops, with Elexon and the database faked."""

import json
import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import psycopg
import pytest
import requests
import yaml

from ingestion.elexon.remit_poller import (
    reread,
    reread_days,
    reread_windows,
    rows_in_window,
)
from ingestion.routing import ContractRejected
from ingestion.source_api import USER_AGENT, decimal_json_dumps
from tests.fakes import (
    connection_returning_in_turn,
    quarantined_records,
    rows_inserted_by_table,
)

DBT_PROJECT = Path(__file__).resolve().parents[1] / "dbt" / "dbt_project.yml"
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "elexon" / "remit_publications.json"
STREAM_URL = "https://data.elexon.co.uk/bmrs/api/v1/datasets/REMIT/stream"
LAST_DAY = date(2026, 11, 8)
NOVEMBER_2_TO_8 = [date(2026, 11, day) for day in range(2, 9)]
# Normal running: the hourly run stored a message 28 minutes before 04:40.
NORMAL_WATERMARK = datetime(2026, 11, 9, 4, 12, tzinfo=timezone.utc)
# The hourly run stopped on 1 October and has not caught up.
OUTAGE_WATERMARK = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
# A quiet evening: no message since 21:30:40 on the last day.
QUIET_EVENING_WATERMARK = datetime(2026, 11, 8, 21, 30, 40, tzinfo=timezone.utc)
FIFTH_OF_NOVEMBER_MIDNIGHT = datetime(2026, 11, 5, tzinfo=timezone.utc)
NOVEMBER_8_START = datetime(2026, 11, 8, tzinfo=timezone.utc)
NOVEMBER_9_START = datetime(2026, 11, 9, tzinfo=timezone.utc)
FIRST_REQUEST_TIME = datetime(2026, 11, 9, 4, 40, 1, tzinfo=timezone.utc)
NOTHING_NEWLY_STORED = (0, None, None)
STORED_COLUMN_COUNT = 18


def utc(text):
    return datetime.fromisoformat(text).astimezone(timezone.utc)


def message_template():
    rows = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"), parse_float=Decimal)
    return rows[0]


def message_published_at(publish_text, mrid):
    return {**message_template(), "mrid": mrid, "publishTime": publish_text}


def one_message_a_day(day):
    """A body with one message published at noon on the requested day."""

    publish_text = f"{day.isoformat()}T12:00:00Z"
    return [message_published_at(publish_text, f"REREAD-{day.isoformat()}")]


def nothing_published(day):
    return []


def requested_day(params):
    return date.fromisoformat(params["publishDateTimeFrom"][:10])


def elexon_serving(bodies_by_day=None, statuses_by_day=None, default=one_message_a_day):
    """Stand in for Elexon's REMIT stream: a scripted body or HTTP status per
    requested day, else default(day)."""

    if bodies_by_day is None:
        bodies_by_day = {}
    if statuses_by_day is None:
        statuses_by_day = {}

    def get(url, params, headers, timeout):
        day = requested_day(params)
        response = MagicMock()
        status = statuses_by_day.get(day)
        if status is not None:
            response.status_code = status
            response.raise_for_status.side_effect = requests.HTTPError(
                f"{status} Error", response=response
            )
            return response
        body = bodies_by_day.get(day)
        if body is None:
            body = default(day)
        response.text = decimal_json_dumps(body)
        return response

    return get


def database(watermark, quarantined=0, newly_stored_rows=None):
    """A fake connection answering the quarantine count, the watermark and one
    newly-stored count per asked day, in turn."""

    if newly_stored_rows is None:
        newly_stored_rows = [NOTHING_NEWLY_STORED] * 7
    return connection_returning_in_turn(
        (quarantined,), (watermark,), *newly_stored_rows
    )


def clock_counting(mock_get):
    """A clock reading FIRST_REQUEST_TIME plus a minute for each request
    already made, so a stamp shows whether it was taken before its request."""

    def now():
        return FIRST_REQUEST_TIME + timedelta(minutes=mock_get.call_count)

    return now


def run_reread(conn, get, last_day=LAST_DAY):
    """Run the re-read with Elexon faked, no pause, and a clock that moves a
    minute per request made. Returns the requests' parameters, the summary or
    the error, and the sleep mock."""

    with (
        patch(
            "ingestion.elexon.remit_poller.requests.get", side_effect=get
        ) as mock_get,
        patch("ingestion.elexon.remit_poller.time.sleep") as mock_sleep,
        patch(
            "ingestion.elexon.remit_poller.request_time",
            side_effect=clock_counting(mock_get),
        ),
    ):
        try:
            outcome = reread(conn, last_day)
        except Exception as error:
            outcome = error
    asked = []
    for call in mock_get.call_args_list:
        asked.append(call.kwargs["params"])
    return asked, outcome, mock_sleep


def days_of(asked):
    days = []
    for params in asked:
        days.append(requested_day(params))
    return days


def answers_with_a_failed_third_day(quarantined_by_it):
    """The database's answers when the third day's request fails in the
    parser: its count of rows quarantined before the failure in place of a
    newly-stored count."""

    answers = [NOTHING_NEWLY_STORED] * 7
    answers[1] = (quarantined_by_it,)
    return answers


def mixed_response(day):
    """A row the contract rejects (revisionNumber not an integer) and a row
    whose publish time is not a time, in one response."""

    rejected = {**one_message_a_day(day)[0], "revisionNumber": "x"}
    malformed = message_published_at("not a time", "REREAD-MALFORMED")
    return [rejected, malformed]


def stored_remit_rows(conn):
    return rows_inserted_by_table(conn).get("raw.elexon_remit", [])


def stored_mrids(conn):
    mrids = []
    for row in stored_remit_rows(conn):
        mrids.append(row[0])
    return mrids


@pytest.mark.parametrize(
    "last_day,expected",
    [
        pytest.param(LAST_DAY, NOVEMBER_2_TO_8, id="8 November"),
        pytest.param(
            date(2026, 12, 3),
            [
                date(2026, 11, 27),
                date(2026, 11, 28),
                date(2026, 11, 29),
                date(2026, 11, 30),
                date(2026, 12, 1),
                date(2026, 12, 2),
                date(2026, 12, 3),
            ],
            id="across a month end",
        ),
        pytest.param(
            date(2026, 10, 25),
            [date(2026, 10, day) for day in range(19, 26)],
            id="up to the autumn clock change",
        ),
    ],
)
def test_the_reread_days_are_the_seven_up_to_the_last_day_oldest_first(
    last_day, expected
):
    assert reread_days(last_day) == expected


def test_a_whole_day_window_runs_from_utc_midnight_to_utc_midnight():
    windows, skipped = reread_windows([date(2026, 10, 25)], NORMAL_WATERMARK)

    assert skipped == []
    assert windows == [
        {
            "day": date(2026, 10, 25),
            "start": utc("2026-10-25T00:00:00+00:00"),
            "request_end": utc("2026-10-26T00:00:00+00:00"),
            "day_end": utc("2026-10-26T00:00:00+00:00"),
        }
    ]


def test_every_day_is_skipped_when_the_hourly_read_stopped_before_them():
    windows, skipped = reread_windows(NOVEMBER_2_TO_8, OUTAGE_WATERMARK)

    assert windows == []
    assert skipped == NOVEMBER_2_TO_8


def test_a_day_starting_at_the_watermark_is_skipped_and_earlier_days_kept():
    windows, skipped = reread_windows(NOVEMBER_2_TO_8, FIFTH_OF_NOVEMBER_MIDNIGHT)

    asked_days = []
    for window in windows:
        asked_days.append(window["day"])
    assert asked_days == [date(2026, 11, 2), date(2026, 11, 3), date(2026, 11, 4)]
    assert skipped == [date(2026, 11, d) for d in range(5, 9)]
    assert windows[-1]["request_end"] == FIFTH_OF_NOVEMBER_MIDNIGHT


def test_a_day_running_past_the_watermark_ends_at_it():
    windows, _skipped = reread_windows([LAST_DAY], QUIET_EVENING_WATERMARK)

    (window,) = windows
    assert window["request_end"] == QUIET_EVENING_WATERMARK
    assert window["day_end"] == utc("2026-11-09T00:00:00+00:00")


@pytest.mark.parametrize(
    "publish_text",
    [
        pytest.param("2026-11-08T00:00:00Z", id="the window's first second"),
        pytest.param("2026-11-08T21:30:40Z", id="exactly the watermark"),
        pytest.param("not a time", id="not a time: fails in the parser"),
        pytest.param(12345, id="not a string: fails the contract"),
    ],
)
def test_a_row_inside_its_window_and_not_after_the_watermark_is_kept(publish_text):
    row = message_published_at(publish_text, "WINDOW-EDGE")

    kept, left_out = rows_in_window(
        [row], NOVEMBER_8_START, NOVEMBER_9_START, QUIET_EVENING_WATERMARK
    )

    assert kept == [row]
    assert left_out == 0


@pytest.mark.parametrize(
    "publish_text",
    [
        pytest.param("2026-11-07T23:59:59Z", id="the second before the window"),
        pytest.param("2026-11-08T21:30:41Z", id="a second after the watermark"),
        pytest.param("2026-11-09T00:00:00Z", id="the next day"),
    ],
)
def test_a_row_outside_its_window_or_after_the_watermark_is_left_out(publish_text):
    row = message_published_at(publish_text, "WINDOW-EDGE")

    kept, left_out = rows_in_window(
        [row], NOVEMBER_8_START, NOVEMBER_9_START, QUIET_EVENING_WATERMARK
    )

    assert kept == []
    assert left_out == 1


def test_the_reread_asks_each_whole_day_oldest_first_in_the_hourly_form():
    with patch("ingestion.elexon.remit_poller.requests.get") as mock_get:
        mock_get.return_value.text = "[]"
        with (
            patch("ingestion.elexon.remit_poller.time.sleep"),
            patch("ingestion.elexon.remit_poller.request_time"),
        ):
            reread(database(NORMAL_WATERMARK), LAST_DAY)

    expected_params = []
    for day in NOVEMBER_2_TO_8:
        expected_params.append(
            {
                "publishDateTimeFrom": f"{day.isoformat()}T00:00Z",
                "publishDateTimeTo": f"{(day + timedelta(days=1)).isoformat()}T00:00Z",
            }
        )
    first_call = mock_get.call_args_list[0]
    asked = []
    for call in mock_get.call_args_list:
        asked.append(call.kwargs["params"])
    assert asked == expected_params
    assert first_call.args == (STREAM_URL,)
    assert first_call.kwargs["headers"] == {"User-Agent": USER_AGENT}
    assert first_call.kwargs["timeout"] == 60


def test_the_reread_pauses_a_second_between_requests():
    _asked, _summary, mock_sleep = run_reread(
        database(NORMAL_WATERMARK), elexon_serving()
    )

    waits = []
    for call in mock_sleep.call_args_list:
        waits.append(call.args)
    assert waits == [(1,)] * 6


def test_the_last_day_ends_at_the_watermark_after_a_quiet_evening():
    asked, _summary, _sleep = run_reread(
        database(QUIET_EVENING_WATERMARK), elexon_serving()
    )

    assert days_of(asked) == NOVEMBER_2_TO_8
    assert asked[-1]["publishDateTimeTo"] == "2026-11-08T21:30Z"


def test_no_day_is_asked_while_the_hourly_read_is_behind_every_day():
    asked, summary, _sleep = run_reread(database(OUTAGE_WATERMARK), elexon_serving())

    assert asked == []
    assert summary["skipped"] == [day.isoformat() for day in NOVEMBER_2_TO_8]


def test_no_day_is_asked_on_an_empty_table():
    asked, summary, _sleep = run_reread(database(None), elexon_serving())

    assert asked == []
    assert summary["asked"] == []


def test_a_row_in_quarantine_holds_the_reread_with_no_request():
    asked, error, _sleep = run_reread(
        database(NORMAL_WATERMARK, quarantined=1), elexon_serving()
    )

    assert asked == []
    assert isinstance(error, ContractRejected)
    assert "1 REMIT rows are in quarantine" in str(error)


def test_http_429_stops_the_reread_at_once():
    asked, error, _sleep = run_reread(
        database(NORMAL_WATERMARK),
        elexon_serving(statuses_by_day={date(2026, 11, 4): 429}),
    )

    assert days_of(asked) == [date(2026, 11, 2), date(2026, 11, 3), date(2026, 11, 4)]
    assert isinstance(error, RuntimeError)
    assert str(error) == (
        "REMIT re-read stopped at 2026-11-04 (HTTP 429); not asked: "
        "2026-11-05, 2026-11-06, 2026-11-07, 2026-11-08"
    )


def test_another_failed_day_does_not_stop_the_reread_but_fails_it():
    asked, error, _sleep = run_reread(
        database(NORMAL_WATERMARK),
        elexon_serving(statuses_by_day={date(2026, 11, 3): 503}),
    )

    assert days_of(asked) == NOVEMBER_2_TO_8
    assert str(error) == 'REMIT re-read days failed: {"2026-11-03": "HTTP 503"}'


def test_a_database_error_stops_the_reread_at_once():
    conn = connection_returning_in_turn(
        (0,), (NORMAL_WATERMARK,), psycopg.OperationalError("connection lost")
    )

    asked, error, _sleep = run_reread(conn, elexon_serving())

    assert days_of(asked) == [date(2026, 11, 2)]
    assert "stopped at 2026-11-02 (OperationalError)" in str(error)


def test_a_body_that_is_not_a_list_fails_its_day():
    asked, error, _sleep = run_reread(
        database(NORMAL_WATERMARK),
        elexon_serving(bodies_by_day={date(2026, 11, 3): {"data": []}}),
    )

    assert len(asked) == 7
    assert str(error) == 'REMIT re-read days failed: {"2026-11-03": "RuntimeError"}'


def test_a_rejected_row_is_quarantined_and_fails_the_reread_after_every_day():
    rejected = {**one_message_a_day(date(2026, 11, 3))[0], "mrid": None}
    conn = database(NORMAL_WATERMARK)

    asked, error, _sleep = run_reread(
        conn,
        elexon_serving(
            bodies_by_day={
                date(2026, 11, 3): [*one_message_a_day(date(2026, 11, 3)), rejected]
            }
        ),
    )

    assert len(asked) == 7
    assert isinstance(error, ContractRejected)
    assert len(quarantined_records(conn)) == 1
    assert "REREAD-2026-11-03" in stored_mrids(conn)


def test_a_malformed_publish_time_fails_its_day_and_quarantines_nothing():
    malformed = message_published_at("not a time", "REREAD-MALFORMED")
    conn = database(
        NORMAL_WATERMARK,
        newly_stored_rows=answers_with_a_failed_third_day(quarantined_by_it=0),
    )

    asked, error, _sleep = run_reread(
        conn,
        elexon_serving(
            bodies_by_day={
                date(2026, 11, 3): [*one_message_a_day(date(2026, 11, 3)), malformed]
            }
        ),
    )

    assert len(asked) == 7
    assert str(error) == 'REMIT re-read days failed: {"2026-11-03": "ValueError"}'
    assert "REREAD-2026-11-03" not in stored_mrids(conn)
    assert quarantined_records(conn) == []


def test_rows_outside_the_window_or_after_the_watermark_are_not_stored():
    body = [
        message_published_at("2026-11-08T12:00:00Z", "INSIDE"),
        message_published_at("2026-11-07T23:59:59Z", "DAY-BEFORE"),
        message_published_at("2026-11-08T21:30:41Z", "AFTER-WATERMARK"),
    ]
    conn = database(QUIET_EVENING_WATERMARK)

    _asked, summary, _sleep = run_reread(
        conn, elexon_serving(bodies_by_day={LAST_DAY: body}, default=nothing_published)
    )

    assert stored_mrids(conn) == ["INSIDE"]
    assert summary["left_out"]["2026-11-08"] == 2


def test_each_stored_row_carries_the_time_taken_just_before_its_request():
    conn = database(NORMAL_WATERMARK)

    run_reread(conn, elexon_serving())

    stored_at = {}
    for row in stored_remit_rows(conn):
        assert len(row) == STORED_COLUMN_COUNT
        stored_at[row[0]] = row[-1]
    assert stored_at["REREAD-2026-11-02"] == FIRST_REQUEST_TIME
    assert stored_at["REREAD-2026-11-08"] == FIRST_REQUEST_TIME + timedelta(minutes=6)


def test_the_summary_records_what_each_day_newly_stored():
    newly_stored_rows = [NOTHING_NEWLY_STORED] * 7
    newly_stored_rows[1] = (2, 90061, 172800)
    conn = database(NORMAL_WATERMARK, newly_stored_rows=newly_stored_rows)

    _asked, summary, _sleep = run_reread(conn, elexon_serving())

    assert summary["newly_stored"]["2026-11-03"] == 2
    assert summary["newly_stored"]["2026-11-02"] == 0
    assert summary["seconds_late"] == {"2026-11-03": [90061, 172800]}
    assert summary["served"]["2026-11-03"] == 1


def test_the_late_warning_checks_from_9_october_and_counts_back_from_today():
    variables = yaml.safe_load(DBT_PROJECT.read_text(encoding="utf-8"))["vars"]

    # The history load of 8 October stored every message late by design.
    assert variables["remit_late_check_from"] == "2026-10-09"
    # Null: the nightly evaluates on today's UTC date.
    assert variables["remit_late_check_today"] is None


def test_a_rejected_row_and_a_malformed_time_in_one_response_hold_the_next_reread(
    caplog,
):
    caplog.set_level(logging.INFO, logger="ingestion.elexon.remit_poller")
    first_run = database(
        NORMAL_WATERMARK,
        newly_stored_rows=answers_with_a_failed_third_day(quarantined_by_it=1),
    )

    asked, error, _sleep = run_reread(
        first_run,
        elexon_serving(
            bodies_by_day={date(2026, 11, 3): mixed_response(date(2026, 11, 3))},
            default=nothing_published,
        ),
    )
    _asked, held, _sleep = run_reread(
        database(NORMAL_WATERMARK, quarantined=1), elexon_serving()
    )

    assert len(asked) == 7
    assert len(quarantined_records(first_run)) == 1
    assert stored_remit_rows(first_run) == []
    assert str(error) == (
        'REMIT re-read days failed: {"2026-11-03": "ValueError"}; rows '
        "quarantined before the failure, which hold the next re-read: "
        '{"2026-11-03": 1}'
    )
    assert isinstance(held, ContractRejected)
    assert '"held": true' in caplog.text
