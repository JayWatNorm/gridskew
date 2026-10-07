"""Source check for BOALF: is the planned key safe, and what do the flags hold?

Calls the live API for one UTC day and the day after it, then prints the
counts recorded in docs/sources/elexon/040_boalf.md.
"""

from collections import Counter
from datetime import datetime, timedelta, timezone

from ingestion.elexon.boalf_poller import fetch

DAY = datetime(2026, 10, 5, tzinfo=timezone.utc)
NEXT_DAY = DAY + timedelta(days=1)
FIELDS_THAT_MUST_NOT_BE_NULL = (
    "nationalGridBmUnit",
    "acceptanceNumber",
    "timeFrom",
    "timeTo",
    "acceptanceTime",
    "bmUnit",
)
FLAG_FIELDS = ("amendmentFlag", "soFlag", "deemedBoFlag", "storFlag", "rrFlag")


def acceptance_of(row):
    return (row["nationalGridBmUnit"], row["acceptanceNumber"])


def ramp_point_of(row):
    return (row["nationalGridBmUnit"], row["acceptanceNumber"], row["timeFrom"])


def count_values_seen_more_than_once(values):
    repeated = 0
    for occurrences in Counter(values).values():
        if occurrences > 1:
            repeated += 1
    return repeated


def units_by_acceptance_number(rows):
    units = {}
    for row in rows:
        number = row["acceptanceNumber"]
        if number not in units:
            units[number] = set()
        units[number].add(row["nationalGridBmUnit"])
    return units


def count_numbers_held_by_several_units(units_by_number):
    shared = 0
    for units in units_by_number.values():
        if len(units) > 1:
            shared += 1
    return shared


def count_nulls(rows, field):
    nulls = 0
    for row in rows:
        if row[field] is None:
            nulls += 1
    return nulls


def count_rows_spanning_periods(rows):
    spanning = 0
    for row in rows:
        if row["settlementPeriodFrom"] != row["settlementPeriodTo"]:
            spanning += 1
    return spanning


rows = fetch(DAY, NEXT_DAY)
next_day_rows = fetch(NEXT_DAY, NEXT_DAY + timedelta(days=1))

acceptances = []
ramp_points = []
units = set()
for row in rows:
    acceptances.append(acceptance_of(row))
    ramp_points.append(ramp_point_of(row))
    units.add(row["nationalGridBmUnit"])

print(f"rows                               : {len(rows):,}")
print(f"distinct units                     : {len(units):,}")
print(f"distinct (unit, acceptance number) : {len(set(acceptances)):,}")

print("ramp points per acceptance         :")
points_in_each_acceptance = Counter(acceptances).values()
acceptances_by_point_count = Counter(points_in_each_acceptance)
for point_count in sorted(acceptances_by_point_count):
    acceptance_count = acceptances_by_point_count[point_count]
    print(f"  {point_count} points: {acceptance_count:,} acceptances")

units_by_number = units_by_acceptance_number(rows)
shared_numbers = count_numbers_held_by_several_units(units_by_number)
print(
    f"acceptance numbers on several units: {shared_numbers:,}"
    f" of {len(units_by_number):,}"
)
repeated_ramp_points = count_values_seen_more_than_once(ramp_points)
print(f"repeated (unit, number, timeFrom)  : {repeated_ramp_points:,}")

for field in FLAG_FIELDS:
    values_of_this_flag = []
    for row in rows:
        values_of_this_flag.append(row[field])
    print(f"{field:<35}: {dict(Counter(values_of_this_flag))}")

spanning = count_rows_spanning_periods(rows)
print(
    f"rows spanning settlement periods   : {spanning:,}"
    f" ({100 * spanning / len(rows):.1f}%)"
)

for field in FIELDS_THAT_MUST_NOT_BE_NULL:
    print(f"null {field:<30}: {count_nulls(rows, field):,}")

# Two consecutive daily requests: what does the second repeat from the first?
next_day_acceptances = set()
next_day_ramp_points = set()
for row in next_day_rows:
    next_day_acceptances.add(acceptance_of(row))
    next_day_ramp_points.add(ramp_point_of(row))

acceptances_in_both = set(acceptances) & next_day_acceptances
ramp_points_in_both = set(ramp_points) & next_day_ramp_points
start_times_in_both = set()
for _unit, _number, time_from in ramp_points_in_both:
    start_times_in_both.add(time_from)

print(f"acceptances in both daily requests : {len(acceptances_in_both):,}")
print(f"ramp points in both daily requests : {len(ramp_points_in_both):,}")
print(f"  their timeFrom values            : {sorted(start_times_in_both)}")
