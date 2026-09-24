import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch
from uuid import UUID

import pytest

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


def test_fixture_is_complete_but_has_nullable_fields(rows):
    rejected, unit_count = validate_extract(rows)

    assert rejected == []
    assert unit_count == len(rows)
    assert rows[5]["elexonBmUnit"] is None


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

    assert any(
        "attributes disagree" in error for f in rejected for error in f["errors"]
    )


def test_duplicate_unit_with_same_eic_is_rejected(rows):
    rows.append(deepcopy(rows[1]))

    rejected, _ = validate_extract(rows)

    assert any("distinct EICs" in error for f in rejected for error in f["errors"])


@pytest.mark.parametrize("value", ["", "   ", "not-a-number", "NaN"])
def test_bad_identity_or_capacity_blocks_publication(rows, value):
    if value in ("", "   "):
        rows[0]["nationalGridBmUnit"] = value
    else:
        rows[0]["generationCapacity"] = value

    rejected, _ = validate_extract(rows)

    assert rejected


def test_blank_capacity_is_accepted_as_unknown(rows):
    rows[0]["generationCapacity"] = "   "

    rejected, _ = validate_extract(rows)

    assert rejected == []


def test_completeness_blocks_large_loss_and_accepts_small_change(rows):
    previous = (UUID(int=1), 100, 100)
    previous_keys = {f"unit-{i}" for i in range(100)}

    with pytest.raises(RuntimeError, match="shrank"):
        check_completeness(rows, len(rows), previous, previous_keys)

    check_completeness(
        [{"nationalGridBmUnit": key} for key in sorted(previous_keys)],
        100,
        previous,
        previous_keys,
    )


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


@pytest.mark.parametrize(
    "value", ["1e999999", "1e-16384", "NaN", "Infinity", "1_000", "\u00a0"]
)
def test_capacity_values_outside_supported_numeric_contract_are_rejected(rows, value):
    rows[0]["generationCapacity"] = value
    rejected, _ = validate_extract(rows)
    assert any("Invalid numeric" in error for f in rejected for error in f["errors"])


@pytest.mark.parametrize(
    "value", [" \t\n\r\f\v", "\t15.400\n", ".25", "-2e3", "1e131071", "1e-16383"]
)
def test_supported_capacity_values_pass(rows, value):
    rows[0]["generationCapacity"] = value
    rejected, _ = validate_extract(rows)
    assert rejected == []


@pytest.mark.parametrize("bad_eic", [[], {}])
def test_unhashable_duplicate_eic_is_quarantined(rows, bad_eic):
    duplicate = deepcopy(rows[1])
    duplicate["eic"] = bad_eic
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


def test_completeness_first_capture_and_same_count_key_churn(rows):
    with pytest.raises(RuntimeError, match="minimum"):
        check_completeness(rows, len(rows), None, set())
    check_completeness(rows, len(rows), None, set(), first_min=len(rows))
    previous_keys = {f"unit-{i}" for i in range(100)}
    replacement = [{"nationalGridBmUnit": f"other-{i}"} for i in range(100)]
    with pytest.raises(RuntimeError, match="unit keys"):
        check_completeness(replacement, 100, (UUID(int=1), 100, 100), previous_keys)


@pytest.mark.parametrize("manifest", [None, (str(UUID(int=2)), 10, 10)])
def test_locked_extract_rejects_missing_or_changed_manifest(manifest):
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value.fetchone.return_value = manifest
    with pytest.raises(RuntimeError, match="not latest"):
        with locked_extract(conn, UUID(int=1)):
            pytest.fail("dbt must not start")
    conn.rollback.assert_called_once()
