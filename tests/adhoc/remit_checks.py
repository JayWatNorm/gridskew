"""Source check for REMIT: which fields identify one publication of a message?

Calls the live API for one calendar month, then prints the counts recorded
in docs/sources/elexon/030_remit.md.
"""

from collections import Counter
from datetime import datetime, timezone

from ingestion.elexon.remit_poller import fetch

MONTH_START = datetime(2026, 9, 1, tzinfo=timezone.utc)
MONTH_END = datetime(2026, 10, 1, tzinfo=timezone.utc)
CANDIDATE_KEYS = (
    ("mrid", "revisionNumber"),
    ("mrid", "revisionNumber", "publishTime"),
    ("mrid", "revisionNumber", "publishTime", "createdTime"),
)
FIELDS_WITH_FEW_VALUES = (
    "messageType",
    "eventType",
    "unavailabilityType",
    "eventStatus",
    "assetType",
)


def count_values_seen_more_than_once(values):
    repeated = 0
    for occurrences in Counter(values).values():
        if occurrences > 1:
            repeated += 1
    return repeated


def key_values(rows, key_fields):
    values = []
    for row in rows:
        key = []
        for field in key_fields:
            key.append(row[field])
        values.append(tuple(key))
    return values


def count_rows_with(rows, field):
    present = 0
    for row in rows:
        if field in row:
            present += 1
    return present


def count_nulls(rows, field):
    nulls = 0
    for row in rows:
        if field in row and row[field] is None:
            nulls += 1
    return nulls


def all_fields(rows):
    fields = set()
    for row in rows:
        fields.update(row)
    return sorted(fields)


def values_of(rows, field):
    values = []
    for row in rows:
        values.append(row.get(field, "<absent>"))
    return dict(Counter(values))


def count_fractional_or_negative(rows, field):
    odd = 0
    for row in rows:
        capacity = row.get(field)
        if capacity is None:
            continue
        if capacity < 0 or capacity % 1 != 0:
            odd += 1
    return odd


rows = fetch(MONTH_START, MONTH_END)

mrids = set()
for row in rows:
    mrids.add(row["mrid"])

print(f"rows                 : {len(rows):,}")
print(f"distinct mrid        : {len(mrids):,}")

for key_fields in CANDIDATE_KEYS:
    repeated = count_values_seen_more_than_once(key_values(rows, key_fields))
    print(f"repeated {key_fields}: {repeated:,}")

print("field                    present    null")
for field in all_fields(rows):
    present = count_rows_with(rows, field)
    print(f"  {field:<22} {present:>7,} {count_nulls(rows, field):>7,}")

for field in FIELDS_WITH_FEW_VALUES:
    print(f"{field}: {values_of(rows, field)}")

for field in ("normalCapacity", "availableCapacity", "unavailableCapacity"):
    odd = count_fractional_or_negative(rows, field)
    print(f"{field} fractional or negative: {odd:,}")
