"""Write tests/fixtures/elexon/remit_publications.json from one real REMIT request.

A day is about 200 messages, some with long outage profiles. This keeps the
first message that shows each trait the tests need, and reports which row was
kept for which trait.
"""

from datetime import datetime, timezone
from pathlib import Path

import simplejson

from ingestion.elexon.remit_poller import fetch

DAY_START = datetime(2026, 9, 26, tzinfo=timezone.utc)
DAY_END = datetime(2026, 9, 27, tzinfo=timezone.utc)
LONGEST_PROFILE_KEPT = 3
FIXTURE_PATH = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "elexon"
    / "remit_publications.json"
)


def publication_of(row):
    return (row["mrid"], row["revisionNumber"], row["publishTime"])


def rows_by_publication(rows):
    grouped = {}
    for row in rows:
        publication = publication_of(row)
        if publication not in grouped:
            grouped[publication] = []
        grouped[publication].append(row)
    return grouped


def rows_published_twice_at_one_time(rows):
    """Two rows that share mrid, revision number and publish time."""
    for same_publication in rows_by_publication(rows).values():
        if len(same_publication) > 1:
            return same_publication
    raise RuntimeError("No publication in this window is repeated")


def is_planned(row):
    return row.get("unavailabilityType") == "Planned"


def is_dismissed(row):
    return row["eventStatus"] == "Dismissed"


def has_no_unit_fields(row):
    return row["messageType"] == "OtherMarketInformation"


def has_short_outage_profile(row):
    profile = row.get("outageProfile")
    return profile is not None and len(profile) <= LONGEST_PROFILE_KEPT


def has_fractional_capacity(row):
    capacity = row.get("unavailableCapacity")
    return capacity is not None and capacity % 1 != 0


TRAITS = {
    "planned": is_planned,
    "dismissed": is_dismissed,
    "no unit fields": has_no_unit_fields,
    "short outage profile": has_short_outage_profile,
    "fractional capacity": has_fractional_capacity,
}


def first_row_with(trait, rows):
    for row in rows:
        if trait(row):
            return row
    raise RuntimeError("No message in this window shows the trait")


rows = fetch(DAY_START, DAY_END)

fixture_rows = []
for row in rows_published_twice_at_one_time(rows):
    fixture_rows.append(row)
    print(f"{'published twice':<22}: {publication_of(row)} {row['createdTime']}")

for trait_name, trait in TRAITS.items():
    row = first_row_with(trait, rows)
    if row not in fixture_rows:
        fixture_rows.append(row)
    print(f"{trait_name:<22}: {publication_of(row)}")

fixture_text = simplejson.dumps(fixture_rows, indent=4, use_decimal=True)
FIXTURE_PATH.write_text(fixture_text + "\n", encoding="utf-8")
print(f"wrote {len(fixture_rows)} rows of {len(rows):,} to {FIXTURE_PATH.name}")
