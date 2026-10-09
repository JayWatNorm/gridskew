import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest
from fakes import connection_returning_in_turn, cursor_of, single_spaced

from ingestion.elexon.contracts import REMIT_SPEC
from ingestion.elexon.remit_poller import (
    HISTORY_START,
    build_windows,
    decimal_json_dumps,
    fetch,
    load,
    parse,
)
from ingestion.elexon.remit_poller import run as run_poller
from ingestion.validation import validate_rows

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "elexon" / "remit_publications.json"
RUN_TIME = datetime(2026, 10, 8, 9, 15, 4, tzinfo=timezone.utc)
FRACTIONAL_CAPACITY_ROW = 5
NO_UNIT_FIELDS_ROW = 4
FIRST_OPTIONAL_COLUMN = 4
EVENT_START_TIME_COLUMN = 14
EVENT_END_TIME_COLUMN = 15
PAYLOAD_COLUMN = 16


@pytest.fixture
def source_rows():
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"), parse_float=Decimal)


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


def test_the_fixture_satisfies_the_contract(source_rows):
    for finding in validate_rows(source_rows, spec=REMIT_SPEC):
        assert finding["errors"] == []


def test_parse_maps_a_message_to_the_database_tuple(source_rows):
    parsed_rows = parse(source_rows, RUN_TIME)
    parsed_row = parsed_rows[FRACTIONAL_CAPACITY_ROW]

    assert len(parsed_rows) == len(source_rows)
    assert parsed_row[:PAYLOAD_COLUMN] == (
        "48X000000000284H-ELXP-RMT-00105746",
        1,
        datetime(2026, 9, 26, 8, 58, 21, tzinfo=timezone.utc),
        datetime(2026, 9, 26, 8, 55, 13, tzinfo=timezone.utc),
        "UnavailabilitiesOfElectricityFacilities",
        "Production unavailability",
        "Planned",
        "Active",
        "E_THMRB-1",
        "THMRB-1",
        None,
        Decimal("49.920"),
        Decimal("0.000"),
        Decimal("49.920"),
        datetime(2026, 9, 26, 9, 0, tzinfo=timezone.utc),
        datetime(2026, 10, 10, 11, 0, tzinfo=timezone.utc),
    )
    assert parsed_row[PAYLOAD_COLUMN].adapted == source_rows[FRACTIONAL_CAPACITY_ROW]
    assert parsed_row[PAYLOAD_COLUMN + 1] == RUN_TIME


def test_parse_stores_null_for_the_fields_a_message_does_not_carry(source_rows):
    parsed_rows = parse(source_rows, RUN_TIME)

    assert parsed_rows[NO_UNIT_FIELDS_ROW][:PAYLOAD_COLUMN] == (
        "20260927STATKRA1-ELXP-RMT-00000001",
        1,
        datetime(2026, 9, 26, 23, 1, 16, tzinfo=timezone.utc),
        datetime(2026, 9, 26, 22, 59, 14, tzinfo=timezone.utc),
        "OtherMarketInformation",
        None,
        None,
        "Active",
        "NO_ASSET",
        None,
        None,
        None,
        None,
        None,
        datetime(2026, 9, 27, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc),
    )


def test_a_message_with_no_end_time_is_accepted_and_stored_with_null(source_rows):
    open_ended_row = dict(source_rows[NO_UNIT_FIELDS_ROW])
    del open_ended_row["eventEndTime"]

    findings = validate_rows([open_ended_row], spec=REMIT_SPEC)
    parsed_rows = parse([open_ended_row], RUN_TIME)

    assert findings[0]["errors"] == []
    assert parsed_rows[0][EVENT_END_TIME_COLUMN] is None


def test_a_message_with_only_the_key_and_start_time_is_accepted(source_rows):
    full_row = source_rows[NO_UNIT_FIELDS_ROW]
    minimal_row = {
        "mrid": full_row["mrid"],
        "revisionNumber": full_row["revisionNumber"],
        "publishTime": full_row["publishTime"],
        "createdTime": full_row["createdTime"],
        "eventStartTime": full_row["eventStartTime"],
    }

    findings = validate_rows([minimal_row], spec=REMIT_SPEC)
    parsed_row = parse([minimal_row], RUN_TIME)[0]

    assert findings[0]["errors"] == []
    assert parsed_row[FIRST_OPTIONAL_COLUMN:EVENT_START_TIME_COLUMN] == (None,) * 10
    assert parsed_row[EVENT_END_TIME_COLUMN] is None


@pytest.mark.parametrize(
    "required_field",
    ["mrid", "revisionNumber", "publishTime", "createdTime", "eventStartTime"],
)
def test_a_message_without_a_key_field_or_start_time_is_rejected(
    source_rows, required_field
):
    row = dict(source_rows[NO_UNIT_FIELDS_ROW])
    del row[required_field]

    findings = validate_rows([row], spec=REMIT_SPEC)

    assert findings[0]["errors"] == [f"Missing required field: {required_field}"]


def test_the_payload_keeps_capacities_as_published(source_rows):
    payload_text = decimal_json_dumps(source_rows[FRACTIONAL_CAPACITY_ROW])

    assert '"normalCapacity": 49.920' in payload_text


def test_load_uses_the_expected_columns_conflict_key_and_commits():
    parsed_rows = [Mock(name="parsed_row")]
    conn = MagicMock()

    with patch("ingestion.elexon.remit_poller.execute_values") as mock_execute_values:
        load(parsed_rows, conn)

    _cursor, insert_sql, inserted_rows = mock_execute_values.call_args.args
    assert single_spaced(insert_sql) == (
        "INSERT INTO raw.elexon_remit (mrid, revision_number, publish_time, "
        "created_time, message_type, event_type, unavailability_type, "
        "event_status, asset_id, affected_unit, fuel_type, normal_capacity, "
        "available_capacity, unavailable_capacity, event_start_time, "
        "event_end_time, payload, retrieved_at) "
        "VALUES %s "
        "ON CONFLICT (mrid, revision_number, publish_time, created_time) "
        "DO NOTHING"
    )
    assert inserted_rows == parsed_rows
    conn.commit.assert_called_once_with()


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


def test_run_on_an_empty_table_fetches_the_history_a_day_at_a_time():
    conn = database_with(None)

    with patch("ingestion.elexon.remit_poller.fetch", return_value=[]) as mock_fetch:
        run_at_run_time(conn)

    first_window = mock_fetch.call_args_list[0].args
    last_window = mock_fetch.call_args_list[-1].args
    assert first_window == (HISTORY_START, HISTORY_START + timedelta(days=1))
    assert last_window == (datetime(2026, 10, 8, tzinfo=timezone.utc), RUN_TIME)
    assert mock_fetch.call_count == 778


def test_run_accepts_a_window_with_no_message():
    conn = database_with(RUN_TIME - timedelta(minutes=30))

    with (
        patch("ingestion.elexon.remit_poller.fetch", return_value=[]),
        patch("ingestion.elexon.remit_poller.process_rows") as mock_process_rows,
    ):
        run_at_run_time(conn)

    mock_process_rows.assert_not_called()


def test_run_rejects_a_response_that_is_not_a_list():
    conn = database_with(RUN_TIME - timedelta(minutes=30))

    with (
        patch("ingestion.elexon.remit_poller.fetch", return_value={}),
        patch("ingestion.elexon.remit_poller.process_rows") as mock_process_rows,
    ):
        with pytest.raises(RuntimeError, match="REMIT API: expected a list"):
            run_at_run_time(conn)

    mock_process_rows.assert_not_called()


def test_run_passes_rows_and_request_context_to_shared_routing(source_rows):
    newest_stored = RUN_TIME - timedelta(minutes=30)
    conn = database_with(newest_stored)

    with (
        patch("ingestion.elexon.remit_poller.fetch", return_value=source_rows),
        patch(
            "ingestion.elexon.remit_poller.process_rows",
            return_value=0,
        ) as mock_process_rows,
    ):
        run_at_run_time(conn)

    mock_process_rows.assert_called_once_with(
        source_rows,
        spec=REMIT_SPEC,
        dataset="REMIT",
        conn=conn,
        retrieved_at=RUN_TIME,
        request_context={
            "publishDateTimeFrom": (newest_stored - timedelta(hours=1)).isoformat(),
            "publishDateTimeTo": RUN_TIME.isoformat(),
        },
        parse_rows=parse,
        load_rows=load,
        payload_dumps=decimal_json_dumps,
        log_missing_optional_fields=False,
    )


def test_run_reads_the_newest_publish_time_only_from_rows_stored_after_it():
    conn = database_with(RUN_TIME - timedelta(minutes=30))

    with patch("ingestion.elexon.remit_poller.fetch", return_value=[]):
        run_at_run_time(conn)

    newest_stored_query = cursor_of(conn).execute.call_args_list[0].args
    assert newest_stored_query == (
        "SELECT max(publish_time) FROM raw.elexon_remit "
        "WHERE publish_time <= retrieved_at",
    )


def test_run_fails_while_a_row_from_an_earlier_run_is_in_quarantine():
    conn = database_with(RUN_TIME - timedelta(minutes=30), quarantined_rows=2)

    with patch("ingestion.elexon.remit_poller.fetch", return_value=[]):
        with pytest.raises(RuntimeError, match="2 REMIT rows are in quarantine"):
            run_at_run_time(conn)

    quarantine_query = cursor_of(conn).execute.call_args_list[1].args
    assert quarantine_query == (
        "SELECT count(*) FROM raw.endpoint_quarantine WHERE dataset = 'REMIT'",
    )


def test_run_loads_the_compatible_rows_before_it_fails(source_rows):
    conn = database_with(RUN_TIME - timedelta(minutes=30), quarantined_rows=1)

    with (
        patch("ingestion.elexon.remit_poller.fetch", return_value=source_rows),
        patch("ingestion.elexon.remit_poller.process_rows") as mock_process_rows,
    ):
        with pytest.raises(RuntimeError, match="1 REMIT rows are in quarantine"):
            run_at_run_time(conn)

    mock_process_rows.assert_called_once()


def test_run_loads_an_earlier_range_again_when_given_its_start():
    reload_from = datetime(2026, 10, 7, 9, 15, 4, tzinfo=timezone.utc)
    quarantined_rows = 0
    conn = connection_returning_in_turn((quarantined_rows,))

    with patch("ingestion.elexon.remit_poller.fetch", return_value=[]) as mock_fetch:
        run_at_run_time(conn, reload_from)

    mock_fetch.assert_called_once_with(reload_from, RUN_TIME)
