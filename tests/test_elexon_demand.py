import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from ingestion.elexon import demand_backfill, demand_poller
from ingestion.routing import ContractRejected
from tests.fakes import cursor_of

FIXTURES = Path(__file__).parent / "fixtures" / "elexon"
DBT_PROJECT = Path(__file__).parent.parent / "dbt" / "dbt_project.yml"
RETRIEVED_AT = datetime(2026, 10, 26, 9, 15, 4, tzinfo=timezone.utc)
PUBLISH_FROM = datetime(2026, 8, 19, tzinfo=timezone.utc)
PUBLISH_TO = datetime(2026, 8, 20, tzinfo=timezone.utc)
NDF_COLUMNS = (
    "settlement_date",
    "settlement_period",
    "start_time",
    "boundary",
    "publish_time",
    "demand",
    "retrieved_at",
)
INDO_COLUMNS = (
    "settlement_date",
    "settlement_period",
    "start_time",
    "publish_time",
    "demand",
    "retrieved_at",
)
AUGUST_20 = date(2025, 8, 20)
AUGUST_21 = date(2025, 8, 21)
AUGUST_22 = date(2025, 8, 22)


def fixture_rows(file_name):
    return json.loads((FIXTURES / file_name).read_text(encoding="utf-8"))["data"]


def tables_written(conn):
    """The table of each insert the fake connection's cursor was given."""

    tables = []
    for call in cursor_of(conn).executemany.call_args_list:
        statement = call.args[0]
        tables.append(statement.split()[2])
    return tables


def run_with_response(conn, dataset, response):
    with patch.object(demand_poller, "fetch", return_value=response):
        return demand_poller.run(conn, dataset, PUBLISH_FROM, PUBLISH_TO)


def window_of(day):
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    return start, start + timedelta(days=1)


def connection_storing(*issue_counts_per_query):
    """A fake connection whose held-day queries return each list in turn.

    Each list holds (publish day, distinct publish times) rows: NDF then INDO
    before the run, and NDF then INDO after it.
    """

    conn = MagicMock()
    cursor_of(conn).fetchall.side_effect = list(issue_counts_per_query)
    return conn


def summary_of(dataset, rows):
    return {"dataset": dataset, "rows": rows, "issues": 48}


def fetched_in_turn(*outcomes):
    """Stand in for demand_poller.run: each call returns or raises the next."""

    return patch("ingestion.elexon.demand_poller.run", side_effect=list(outcomes))


def test_fetch_asks_for_one_dataset_published_in_a_utc_window():
    british_summer_time = timezone(timedelta(hours=1))
    publish_from = datetime(2025, 8, 21, 1, 0, tzinfo=british_summer_time)
    publish_to = datetime(2025, 8, 22, tzinfo=timezone.utc)

    with patch("ingestion.elexon.demand_poller.requests.get") as get:
        get.return_value.json.return_value = {"data": []}
        body = demand_poller.fetch("INDO", publish_from, publish_to)

    assert get.call_args.args[0] == (
        "https://data.elexon.co.uk/bmrs/api/v1/datasets/INDO"
    )
    assert get.call_args.kwargs["params"] == {
        "publishDateTimeFrom": "2025-08-21T00:00Z",
        "publishDateTimeTo": "2025-08-22T00:00Z",
    }
    assert body == {"data": []}


def test_parse_ndf_maps_each_source_field_to_its_own_column():
    # The period starts at 23:00 UTC on 19 August: settlement date 20 August.
    source_row = fixture_rows("ndf_truncated.json")[2]

    (parsed_row,) = demand_poller.parse_ndf([source_row], RETRIEVED_AT)

    assert dict(zip(NDF_COLUMNS, parsed_row, strict=True)) == {
        "settlement_date": date(2026, 8, 20),
        "settlement_period": 1,
        "start_time": datetime(2026, 8, 19, 23, 0, tzinfo=timezone.utc),
        "boundary": "N",
        "publish_time": datetime(2026, 8, 19, 10, 49, tzinfo=timezone.utc),
        "demand": 21930,
        "retrieved_at": RETRIEVED_AT,
    }


def test_parse_indo_maps_each_source_field_to_its_own_column():
    source_row = fixture_rows("indo.json")[0]

    (parsed_row,) = demand_poller.parse_indo([source_row], RETRIEVED_AT)

    assert dict(zip(INDO_COLUMNS, parsed_row, strict=True)) == {
        "settlement_date": date(2026, 8, 19),
        "settlement_period": 24,
        "start_time": datetime(2026, 8, 19, 10, 30, tzinfo=timezone.utc),
        "publish_time": datetime(2026, 8, 19, 11, 0, tzinfo=timezone.utc),
        "demand": 22748,
        "retrieved_at": RETRIEVED_AT,
    }


@pytest.mark.parametrize(
    ("parse", "load", "file_name", "conflict_key"),
    [
        pytest.param(
            demand_poller.parse_ndf,
            demand_poller.load_ndf,
            "ndf_truncated.json",
            "(settlement_date, settlement_period, boundary, publish_time)",
            id="NDF",
        ),
        pytest.param(
            demand_poller.parse_indo,
            demand_poller.load_indo,
            "indo.json",
            "(settlement_date, settlement_period, publish_time)",
            id="INDO",
        ),
    ],
)
def test_a_loader_ignores_an_issue_already_stored_on_its_key(
    parse, load, file_name, conflict_key
):
    conn = MagicMock()

    load(parse(fixture_rows(file_name), RETRIEVED_AT), conn)

    statement = cursor_of(conn).executemany.call_args.args[0]
    assert statement.endswith(f"ON CONFLICT {conflict_key} DO NOTHING")


def test_a_whole_window_loads_every_row_and_counts_its_issues():
    conn = MagicMock()

    summary = run_with_response(
        conn, "NDF", {"data": fixture_rows("ndf_truncated.json")}
    )

    assert tables_written(conn) == ["raw.elexon_ndf"]
    assert len(cursor_of(conn).executemany.call_args.args[1]) == 6
    assert summary["rows"] == 6
    assert summary["issues"] == 2


def test_an_empty_window_is_valid_and_writes_nothing():
    conn = MagicMock()

    summary = run_with_response(conn, "INDO", {"data": []})

    assert summary["rows"] == 0
    assert summary["issues"] == 0
    conn.cursor.assert_not_called()


@pytest.mark.parametrize(
    "response",
    [
        pytest.param([], id="bare-list"),
        pytest.param({"data": None}, id="null-data"),
        pytest.param({"metadata": {}}, id="no-data"),
    ],
)
def test_a_response_without_a_data_list_fails_before_any_write(response):
    conn = MagicMock()

    with pytest.raises(
        RuntimeError,
        match="Invalid response returned from Elexon INDO API: expected a data list",
    ):
        run_with_response(conn, "INDO", response)

    conn.cursor.assert_not_called()


def test_a_rejected_row_is_quarantined_and_no_row_of_its_window_loads():
    rows = fixture_rows("indo.json")
    rows[1]["demand"] = None
    conn = MagicMock()

    with pytest.raises(ContractRejected, match="Quarantined 1 rows"):
        run_with_response(conn, "INDO", {"data": rows})

    assert tables_written(conn) == ["raw.endpoint_quarantine"]


def test_the_backfill_walks_every_publish_day_from_20_august_2025_to_17_august_2026():
    days = demand_backfill.publish_days()

    assert days[0] == date(2025, 8, 20)
    assert days[-1] == date(2026, 8, 17)
    assert len(days) == 363
    assert len(set(days)) == 363


def test_the_backfill_covers_the_analysis_window_and_the_issues_around_it():
    # Two publish days before the window's first settlement date hold its last
    # forecast; the day after its last one holds that day's first outturns.
    variables = yaml.safe_load(DBT_PROJECT.read_text(encoding="utf-8"))["vars"]
    window_from = date.fromisoformat(variables["demand_history_from"])
    window_until = date.fromisoformat(variables["demand_history_until"])

    assert demand_backfill.FIRST_PUBLISH_DAY == window_from - timedelta(days=2)
    assert demand_backfill.LAST_PUBLISH_DAY == window_until + timedelta(days=1)


def test_a_day_is_held_only_with_48_distinct_publish_times():
    held = demand_backfill.days_with_every_issue(
        [(AUGUST_20, 48), (AUGUST_21, 47), (AUGUST_22, 49)]
    )

    assert held == {AUGUST_20, AUGUST_22}


def test_held_days_are_skipped_and_the_others_fetched_by_utc_day_ndf_first():
    conn = connection_storing(
        [(AUGUST_21, 48)], [(AUGUST_20, 48), (AUGUST_22, 50)], [], []
    )

    with fetched_in_turn(
        summary_of("NDF", 70), summary_of("NDF", 80), summary_of("INDO", 48)
    ) as mock_run:
        report = demand_backfill.backfill(
            conn, [AUGUST_20, AUGUST_21, AUGUST_22], pause_seconds=0
        )

    requested = []
    for call in mock_run.call_args_list:
        _conn, dataset, publish_from, publish_to = call.args
        requested.append((dataset, publish_from, publish_to))
    assert requested == [
        ("NDF", *window_of(AUGUST_20)),
        ("NDF", *window_of(AUGUST_22)),
        ("INDO", *window_of(AUGUST_21)),
    ]
    assert report["NDF"]["skipped_as_held"] == 1
    assert report["INDO"]["skipped_as_held"] == 2


def test_a_failed_day_does_not_stop_the_days_after_it():
    conn = connection_storing([], [(AUGUST_20, 48)], [(AUGUST_21, 48)], [])

    with fetched_in_turn(
        RuntimeError("503 Server Error"), summary_of("NDF", 75), summary_of("INDO", 48)
    ):
        report = demand_backfill.backfill(conn, [AUGUST_20, AUGUST_21], pause_seconds=0)

    assert report["NDF"]["failed"] == {"2025-08-20": "503 Server Error"}
    assert report["NDF"]["fetched"] == 1
    assert report["INDO"]["failed"] == {}
    assert report["INDO"]["fetched"] == 1
    assert demand_backfill.exit_code(report) == 1
    # Four held-day reads (two datasets, before and after) and the failed day.
    assert conn.rollback.call_count == 5


def test_a_day_still_short_after_its_fetch_is_listed_but_is_not_a_failure():
    # Both NDF days are fetched. 20 August is then held; 21 August stores only
    # 47 distinct publish times, so it is not, and the next run fetches it again.
    held_indo_days = [(AUGUST_20, 48), (AUGUST_21, 48)]
    conn = connection_storing(
        [], held_indo_days, [(AUGUST_20, 48), (AUGUST_21, 47)], held_indo_days
    )

    with fetched_in_turn(summary_of("NDF", 60), summary_of("NDF", 58)):
        report = demand_backfill.backfill(conn, [AUGUST_20, AUGUST_21], pause_seconds=0)

    assert report["NDF"]["not_held_after_run"] == ["2025-08-21"]
    assert report["INDO"]["not_held_after_run"] == []
    assert demand_backfill.exit_code(report) == 0


def test_a_dry_run_makes_no_request_and_lists_the_days_to_fetch():
    conn = connection_storing([(AUGUST_21, 48)], [(AUGUST_20, 47)])

    with patch("ingestion.elexon.demand_poller.run") as mock_run:
        plan = demand_backfill.plan(conn, [AUGUST_20, AUGUST_21])

    mock_run.assert_not_called()
    assert plan == {
        "first_day": "2025-08-20",
        "last_day": "2025-08-21",
        "days": 2,
        "NDF": {"held": 1, "days_to_fetch": ["2025-08-20"]},
        "INDO": {"held": 0, "days_to_fetch": ["2025-08-20", "2025-08-21"]},
        "requests_to_make": 3,
    }
