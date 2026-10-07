"""Write tests/fixtures/elexon/boalf_stream.json from one real BOALF request.

A full day is about 27,000 rows, too many to commit. This keeps every row of
the first acceptance that shows each trait the tests and models need, and
reports which acceptance was kept for which trait.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ingestion.elexon.boalf_poller import fetch

DAY = datetime(2026, 10, 5, tzinfo=timezone.utc)
WINDOW_END_TEXT = (DAY + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
FIXTURE_PATH = (
    Path(__file__).resolve().parents[1] / "fixtures" / "elexon" / "boalf_stream.json"
)


def acceptance_of(row):
    return (row["nationalGridBmUnit"], row["acceptanceNumber"])


def rows_by_acceptance(rows):
    grouped = {}
    for row in rows:
        acceptance = acceptance_of(row)
        if acceptance not in grouped:
            grouped[acceptance] = []
        grouped[acceptance].append(row)
    return grouped


def has_three_point_ramp(acceptance_rows):
    if len(acceptance_rows) < 3:
        return False
    for row in acceptance_rows:
        if row["levelFrom"] != row["levelTo"]:
            return True
    return False


def spans_settlement_periods(acceptance_rows):
    for row in acceptance_rows:
        if row["settlementPeriodFrom"] != row["settlementPeriodTo"]:
            return True
    return False


def is_system_flagged(acceptance_rows):
    for row in acceptance_rows:
        if row["soFlag"]:
            return True
    return False


def has_negative_level(acceptance_rows):
    for row in acceptance_rows:
        if row["levelFrom"] < 0 or row["levelTo"] < 0:
            return True
    return False


def reaches_window_end(acceptance_rows):
    for row in acceptance_rows:
        if row["timeFrom"] == WINDOW_END_TEXT:
            return True
    return False


TRAITS = {
    "three-point ramp": has_three_point_ramp,
    "spans settlement periods": spans_settlement_periods,
    "system flagged": is_system_flagged,
    "negative level": has_negative_level,
    "reaches the window end": reaches_window_end,
}


def first_acceptance_with(trait, grouped):
    for acceptance, acceptance_rows in grouped.items():
        if trait(acceptance_rows):
            return acceptance
    raise RuntimeError("No acceptance in this window shows the trait")


def first_two_units_sharing_a_number(grouped):
    first_unit_by_number = {}
    for unit, number in grouped:
        if number in first_unit_by_number:
            return (first_unit_by_number[number], number), (unit, number)
        first_unit_by_number[number] = unit
    raise RuntimeError("No acceptance number is shared by two units")


rows = fetch(DAY, DAY + timedelta(days=1))
grouped = rows_by_acceptance(rows)

kept = set()
for trait_name, trait in TRAITS.items():
    acceptance = first_acceptance_with(trait, grouped)
    kept.add(acceptance)
    print(f"{trait_name:<26}: {acceptance}")

for acceptance in first_two_units_sharing_a_number(grouped):
    kept.add(acceptance)
    print(f"{'number on two units':<26}: {acceptance}")

fixture_rows = []
for row in rows:
    if acceptance_of(row) in kept:
        fixture_rows.append(row)

FIXTURE_PATH.write_text(json.dumps(fixture_rows, indent=4) + "\n", encoding="utf-8")
print(f"wrote {len(fixture_rows)} rows of {len(rows):,} to {FIXTURE_PATH.name}")
