import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch
from uuid import UUID

import pytest
from fakes import connection_returning, connection_returning_in_turn

from ingestion.elexon.bmunits_poller import (
    check_completeness,
    load_extract,
    locked_extract,
    run,
    validate_extract,
)

FIXTURE = Path(__file__).parent / "fixtures" / "elexon" / "bmunits_truncated.json"
RETRIEVED_AT = datetime(2026, 9, 23, 19, 0, tzinfo=timezone.utc)


@pytest.fixture
def rows():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def error_messages(rejected):
    """Return every error message from every rejected row as one list."""

    messages = []
    for finding in rejected:
        messages.extend(finding["errors"])
    return messages


def contains(messages, text):
    """Return True when any message includes `text`."""

    return any(text in message for message in messages)


def units_named(prefix, count):
    """Return `count` source rows that hold only a distinct unit key."""

    return [{"nationalGridBmUnit": f"{prefix}-{number}"} for number in range(count)]


def test_distinct_eics_for_one_unit_preserve_both_source_rows(rows):
    second = deepcopy(rows[1])
    second["eic"] = "48W00001ACHRW-1R"
    rows.insert(2, second)

    rejected, unit_count = validate_extract(rows)

    assert rejected == []
    assert unit_count == len(rows) - 1


def test_duplicate_unit_with_conflicting_fuel_is_rejected(rows):
    second = deepcopy(rows[1])
    second["eic"] = "another-eic"
    second["fuelType"] = "CCGT"
    rows.append(second)

    rejected, _ = validate_extract(rows)

    assert contains(error_messages(rejected), "attributes disagree")


def test_duplicate_unit_with_same_eic_is_rejected(rows):
    rows.append(deepcopy(rows[1]))

    rejected, _ = validate_extract(rows)

    assert contains(error_messages(rejected), "distinct EICs")


@pytest.mark.parametrize("value", ["", "   "])
def test_blank_unit_identity_blocks_publication(rows, value):
    rows[0]["nationalGridBmUnit"] = value

    rejected, _ = validate_extract(rows)

    assert contains(error_messages(rejected), "Blank nationalGridBmUnit")


def test_blank_capacity_is_accepted_as_unknown(rows):
    rows[0]["generationCapacity"] = "   "

    rejected, _ = validate_extract(rows)

    assert rejected == []


def test_completeness_blocks_a_loss_of_more_than_five_percent():
    previous = (UUID(int=1), 100, 100)
    previous_keys = {f"unit-{number}" for number in range(100)}
    remaining_units = units_named("unit", 94)

    with pytest.raises(RuntimeError, match="shrank"):
        check_completeness(remaining_units, 94, previous, previous_keys)


def test_completeness_accepts_a_loss_of_exactly_five_percent():
    previous = (UUID(int=1), 100, 100)
    previous_keys = {f"unit-{number}" for number in range(100)}
    remaining_units = units_named("unit", 95)

    check_completeness(remaining_units, 95, previous, previous_keys)


def test_load_rolls_back_if_raw_rows_fail(rows):
    conn = MagicMock()

    with patch(
        "ingestion.elexon.bmunits_poller.execute_values",
        side_effect=OSError("database failure"),
    ):
        with pytest.raises(OSError, match="database failure"):
            load_extract(conn, rows, RETRIEVED_AT, len(rows))

    conn.rollback.assert_called_once_with()
    conn.commit.assert_not_called()


def test_run_quarantines_bad_row_without_publishing_extract(rows):
    rows[0]["fpnFlag"] = "true"
    conn = Mock()

    with (
        patch("ingestion.elexon.bmunits_poller.fetch", return_value=rows),
        patch("ingestion.elexon.bmunits_poller.quarantine_rows") as quarantine,
        patch("ingestion.elexon.bmunits_poller.load_extract") as load,
    ):
        with pytest.raises(RuntimeError, match="Rejected 1"):
            run(conn)

    quarantine.assert_called_once()
    load.assert_not_called()


@pytest.mark.parametrize("value", ["1e999999", "1e-16384", "NaN", "1_000", "\u00a0"])
def test_capacity_values_outside_supported_numeric_contract_are_rejected(rows, value):
    rows[0]["generationCapacity"] = value

    rejected, _ = validate_extract(rows)

    assert contains(error_messages(rejected), "Invalid numeric")


@pytest.mark.parametrize(
    "value", [" \t\n\r\f\v", "\t15.400\n", ".25", "-2e3", "1e131071", "1e-16383"]
)
def test_supported_capacity_values_pass(rows, value):
    rows[0]["generationCapacity"] = value

    rejected, _ = validate_extract(rows)

    assert rejected == []


def test_unhashable_duplicate_eic_is_quarantined(rows):
    duplicate = deepcopy(rows[1])
    duplicate["eic"] = []
    rows.append(duplicate)

    with (
        patch("ingestion.elexon.bmunits_poller.fetch", return_value=rows),
        patch("ingestion.elexon.bmunits_poller.quarantine_rows") as quarantine,
        patch("ingestion.elexon.bmunits_poller.load_extract") as load,
    ):
        with pytest.raises(RuntimeError, match="Rejected 1"):
            run(Mock())

    quarantine.assert_called_once()
    load.assert_not_called()


def test_first_capture_one_row_below_the_minimum_is_rejected():
    first_units = units_named("unit", 2499)

    with pytest.raises(RuntimeError, match="minimum"):
        check_completeness(first_units, 2499, None, set())


def test_first_capture_at_the_minimum_is_accepted():
    first_units = units_named("unit", 2500)

    check_completeness(first_units, 2500, None, set())


def test_same_count_with_different_unit_keys_is_rejected():
    previous = (UUID(int=1), 100, 100)
    previous_keys = {f"unit-{number}" for number in range(100)}
    replacement = units_named("other", 100)

    with pytest.raises(RuntimeError, match="unit keys"):
        check_completeness(replacement, 100, previous, previous_keys)


@pytest.mark.parametrize("manifest", [None, (str(UUID(int=2)), 10, 10)])
def test_locked_extract_rejects_missing_or_changed_manifest(manifest):
    conn = connection_returning(manifest)

    with pytest.raises(RuntimeError, match="not latest"):
        with locked_extract(conn, UUID(int=1)):
            pytest.fail("dbt must not start")

    conn.rollback.assert_called_once()


def test_locked_extract_rejects_a_manifest_whose_counts_differ_from_stored_rows():
    manifest = (str(UUID(int=1)), 10, 10)
    stored_row_and_unit_counts = (9, 10)
    conn = connection_returning_in_turn(manifest, stored_row_and_unit_counts)

    with pytest.raises(RuntimeError, match="counts do not match"):
        with locked_extract(conn, UUID(int=1)):
            pytest.fail("dbt must not start")

    conn.rollback.assert_called_once()
