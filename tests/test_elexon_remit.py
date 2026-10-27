import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest

from ingestion.elexon.contracts import REMIT_SPEC
from ingestion.elexon.remit_poller import (
    HISTORY_START,
    build_windows,
    fetch,
    parse,
)
from ingestion.elexon.remit_poller import run as run_poller
from ingestion.routing import ContractRejected
from ingestion.validation import validate_rows
from tests.fakes import (
    connection_returning_in_turn,
    cursor_of,
    quarantined_records,
    rows_inserted_by_table,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "elexon" / "remit_publications.json"
RUN_TIME = datetime(2026, 10, 8, 9, 15, 4, tzinfo=timezone.utc)
REMIT_COLUMNS = (
    "mrid",
    "revision_number",
    "publish_time",
    "created_time",
    "message_type",
    "event_type",
    "unavailability_type",
    "event_status",
    "asset_id",
    "affected_unit",
    "fuel_type",
    "normal_capacity",
    "available_capacity",
    "unavailable_capacity",
    "event_start_time",
    "event_end_time",
    "payload",
    "retrieved_at",
)


@pytest.fixture
def source_rows():
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"), parse_float=Decimal)


def message_with_mrid(source_rows, mrid):
    (message,) = [row for row in source_rows if row["mrid"] == mrid]
    return message


def database_with(newest_stored_publish_time, quarantined_rows=0):
    """A fake connection that answers the poller's two questions in turn."""

    return connection_returning_in_turn(
        (newest_stored_publish_time,), (quarantined_rows,)
    )


def run_at_run_time(conn, first_publish_time=None):
    """Run the poller with the clock fixed at RUN_TIME and no pause between requests."""

    with (
        patch("ingestion.elexon.remit_poller.datetime") as mock_datetime,
        patch("ingestion.elexon.remit_poller.time.sleep"),
    ):
        mock_datetime.now.return_value = RUN_TIME
        run_poller(conn, first_publish_time)


def test_fetch_asks_for_the_window_in_utc_and_keeps_decimals():
    british_summer_time = timezone(timedelta(hours=1))
    publish_from = datetime(2026, 9, 26, 21, 37, 16, tzinfo=british_summer_time)
    publish_to = datetime(2026, 9, 26, 22, 15, 4, tzinfo=timezone.utc)

    with patch("ingestion.elexon.remit_poller.requests.get") as mock_get:
        mock_get.return_value.text = '[{"normalCapacity": 49.920}]'
        rows = fetch(publish_from, publish_to)

    assert mock_get.call_args.kwargs["params"] == {
        "publishDateTimeFrom": "2026-09-26T20:37Z",
        "publishDateTimeTo": "2026-09-26T22:15Z",
    }
    assert rows == [{"normalCapacity": Decimal("49.920")}]
    assert str(rows[0]["normalCapacity"]) == "49.920"


def test_parse_maps_a_message_to_the_database_tuple(source_rows):
    source_row = message_with_mrid(source_rows, "48X000000000284H-ELXP-RMT-00105746")

    (parsed_row,) = parse([source_row], RUN_TIME)

    parsed = dict(zip(REMIT_COLUMNS, parsed_row, strict=True))
    assert parsed.pop("payload").obj == source_row
    assert parsed == {
        "mrid": "48X000000000284H-ELXP-RMT-00105746",
        "revision_number": 1,
        "publish_time": datetime(2026, 9, 26, 8, 58, 21, tzinfo=timezone.utc),
        "created_time": datetime(2026, 9, 26, 8, 55, 13, tzinfo=timezone.utc),
        "message_type": "UnavailabilitiesOfElectricityFacilities",
        "event_type": "Production unavailability",
        "unavailability_type": "Planned",
        "event_status": "Active",
        "asset_id": "E_THMRB-1",
        "affected_unit": "THMRB-1",
        "fuel_type": None,
        "normal_capacity": Decimal("49.920"),
        "available_capacity": Decimal("0.000"),
        "unavailable_capacity": Decimal("49.920"),
        "event_start_time": datetime(2026, 9, 26, 9, 0, tzinfo=timezone.utc),
        "event_end_time": datetime(2026, 10, 10, 11, 0, tzinfo=timezone.utc),
        "retrieved_at": RUN_TIME,
    }


def test_a_message_with_only_the_key_and_start_time_is_accepted():
    minimal_row = {
        "mrid": "20260927STATKRA1-ELXP-RMT-00000001",
        "revisionNumber": 1,
        "publishTime": "2026-09-26T23:01:16Z",
        "createdTime": "2026-09-26T22:59:14Z",
        "eventStartTime": "2026-09-27T00:00:00Z",
    }

    (finding,) = validate_rows([minimal_row], spec=REMIT_SPEC)
    (parsed_row,) = parse([minimal_row], RUN_TIME)

    assert finding["errors"] == []
    stored_columns = []
    for column, value in zip(REMIT_COLUMNS, parsed_row, strict=True):
        if value is not None:
            stored_columns.append(column)
    assert stored_columns == [
        "mrid",
        "revision_number",
        "publish_time",
        "created_time",
        "event_start_time",
        "payload",
        "retrieved_at",
    ]


def test_build_windows_covers_the_range_in_steps_of_at_most_a_day():
    range_start = datetime(2026, 10, 1, 6, 0, tzinfo=timezone.utc)
    range_end = datetime(2026, 10, 3, 9, 30, tzinfo=timezone.utc)

    assert build_windows(range_start, range_end) == [
        (range_start, datetime(2026, 10, 2, 6, 0, tzinfo=timezone.utc)),
        (
            datetime(2026, 10, 2, 6, 0, tzinfo=timezone.utc),
            datetime(2026, 10, 3, 6, 0, tzinfo=timezone.utc),
        ),
        (datetime(2026, 10, 3, 6, 0, tzinfo=timezone.utc), range_end),
    ]


def test_run_starts_one_hour_before_the_newest_stored_message():
    newest_stored = datetime(2026, 10, 8, 8, 50, 31, tzinfo=timezone.utc)
    conn = database_with(newest_stored)

    with patch("ingestion.elexon.remit_poller.fetch", return_value=[]) as mock_fetch:
        run_at_run_time(conn)

    mock_fetch.assert_called_once_with(newest_stored - timedelta(hours=1), RUN_TIME)


def test_run_on_an_empty_table_fetches_everything_from_the_history_start():
    conn = database_with(None)

    with patch("ingestion.elexon.remit_poller.fetch", return_value=[]) as mock_fetch:
        run_at_run_time(conn)

    first_window_start, _ = mock_fetch.call_args_list[0].args
    _, last_window_end = mock_fetch.call_args_list[-1].args
    assert first_window_start == HISTORY_START
    assert last_window_end == RUN_TIME


def test_run_rejects_a_response_that_is_not_a_list():
    conn = database_with(RUN_TIME - timedelta(minutes=30))

    with (
        patch("ingestion.elexon.remit_poller.fetch", return_value={}),
        patch("ingestion.elexon.remit_poller.process_rows") as mock_process_rows,
    ):
        with pytest.raises(RuntimeError, match="REMIT API: expected a list"):
            run_at_run_time(conn)

    mock_process_rows.assert_not_called()


def test_run_stores_valid_messages_and_quarantines_the_rest_with_published_digits(
    source_rows,
):
    valid_message = message_with_mrid(source_rows, "48X000000000284H-ELXP-RMT-00105746")
    message_without_mrid = {**valid_message, "mrid": None}
    conn = database_with(RUN_TIME - timedelta(minutes=30), quarantined_rows=1)

    with patch(
        "ingestion.elexon.remit_poller.fetch",
        return_value=[valid_message, message_without_mrid],
    ):
        with pytest.raises(ContractRejected, match="1 REMIT rows are in quarantine"):
            run_at_run_time(conn)

    stored_rows = rows_inserted_by_table(conn)["raw.elexon_remit"]
    (record,) = quarantined_records(conn)
    payload = record["payload"]
    assert len(stored_rows) == 1
    assert record["dataset"] == "REMIT"
    assert '"normalCapacity": 49.920' in payload.dumps(payload.obj)


def test_run_reads_the_newest_publish_time_only_from_rows_stored_after_it():
    conn = database_with(RUN_TIME - timedelta(minutes=30))

    with patch("ingestion.elexon.remit_poller.fetch", return_value=[]):
        run_at_run_time(conn)

    (newest_stored_query,) = cursor_of(conn).execute.call_args_list[0].args
    assert "publish_time <= retrieved_at" in " ".join(newest_stored_query.split())


def test_run_loads_the_compatible_rows_before_it_fails(source_rows):
    conn = database_with(RUN_TIME - timedelta(minutes=30), quarantined_rows=2)

    with (
        patch("ingestion.elexon.remit_poller.fetch", return_value=source_rows),
        patch("ingestion.elexon.remit_poller.process_rows") as mock_process_rows,
    ):
        with pytest.raises(ContractRejected, match="2 REMIT rows are in quarantine"):
            run_at_run_time(conn)

    mock_process_rows.assert_called_once()


def test_run_loads_an_earlier_range_again_when_given_its_start():
    reload_from = datetime(2026, 10, 7, 9, 15, 4, tzinfo=timezone.utc)
    quarantined_rows = 0
    conn = connection_returning_in_turn((quarantined_rows,))

    with patch("ingestion.elexon.remit_poller.fetch", return_value=[]) as mock_fetch:
        run_at_run_time(conn, reload_from)

    mock_fetch.assert_called_once_with(reload_from, RUN_TIME)
