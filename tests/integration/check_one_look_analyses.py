"""Check the Q1, Q10, Q25, Q37, Q38, Q8, Q28, Q29 and Q30 analyses on worked rows.

CI only runs analyses with limit 0, so their arithmetic is never exercised.
This compiles them with small populations, points each at temporary tables
of hand-worked rows instead of the relations it reads, and compares every
listed cell with the value worked out in the comments. It refuses to run
unless pointed at a local disposable gridskew_dev.

Run from the repository root with the dbt environment set, for example:
    GRIDSKEW_DISPOSABLE_TEST=1 DBT_HOST=localhost DBT_DBNAME=gridskew_dev ...
    python -m tests.integration.check_one_look_analyses
"""

import hashlib
import math
import os
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction

import psycopg

from tests.integration.check_analyses_compile import (
    compile_analyses,
    disposable_connection,
)

Q10 = "q10_forecast_accuracy_by_lead_time"
Q25 = "q25_instruction_fragmentation"
READ_UNTIL = "2026-09-05 00:00:00+00"
# Q29's worked window: the 50-period day 26 October 2025 to the 46-period day
# 29 March 2026.
Q29_WINDOW_VARS = {
    "final_forecast_error_from": "2025-10-26",
    "final_forecast_error_until": "2026-03-29",
}
# Q30's worked window: 25 to 27 October 2025, round the 50-period day.
Q30_WINDOW_VARS = {"q30_window_from": "2025-10-25", "q30_window_until": "2025-10-27"}
REACHED_VARS = {
    "analysis_read_until": READ_UNTIL,
    "forecast_drift_half_hours_needed": 7,
    **Q29_WINDOW_VARS,
    **Q30_WINDOW_VARS,
}
NOT_REACHED_VARS = {
    "analysis_read_until": READ_UNTIL,
    "forecast_drift_half_hours_needed": 8,
}
# The draw count of every bootstrap in the analyses.
BOOTSTRAP_DRAWS = 1000

# Cases on the project's own values of the rule boundaries that are vars (the
# part not seen after 2026-09-10 05:30 UTC and the poll schedule switch at
# slot 11:00 that day), with a cutoff later than both, so a mistyped var fails.
PROJECT_VALUES_VARS = {
    "analysis_read_until": "2026-11-02 00:00:00+00",
    "forecast_drift_half_hours_needed": 1,
}
# Slot polls after that cutoff, (slot, first poll in its slot): three slots
# open the forecast gates; without the last, two do not.
PROJECT_VALUES_LOOK_AHEAD = (
    ("2026-11-02 00:30+00", True),
    ("2026-11-02 01:00+00", True),
    ("2026-11-02 01:00+00", False),
    ("2026-11-02 01:30+00", True),
)


def project_values_look_ahead(gate_open=True):
    """The look-ahead slot polls as SQL values: (poll_slot, is_first_poll_in_slot)."""

    polls = PROJECT_VALUES_LOOK_AHEAD
    if not gate_open:
        polls = PROJECT_VALUES_LOOK_AHEAD[:-1]
    rows = []
    for poll_slot, is_first in polls:
        rows.append(f"('{poll_slot}', {str(is_first).lower()})")
    return ", ".join(rows)


# Q1's verdict on drift rows: (period_start UTC, drift, complete, drift as
# published, complete as published). Each case is one insert.
Q1_VERDICT = "q1_forecast_drift_verdict"
Q1_VERDICT_FIXTURES = """
create temporary table synthetic_drifts (
    period_start timestamptz,
    drift integer,
    has_complete_trajectory boolean,
    as_published_drift integer,
    as_published_has_complete_trajectory boolean
) on commit drop;
{drift_rows};
create temporary table synthetic_slot_polls (
    poll_slot timestamptz,
    is_first_poll_in_slot boolean
) on commit drop;
insert into synthetic_slot_polls values {look_ahead_polls};
"""
Q1_VERDICT_TABLES = {
    "int_carbon_error_by_period": "pg_temp.synthetic_drifts",
    "int_carbon_forecast__revisions": "pg_temp.synthetic_slot_polls",
}

# The exact half weight: Monday 14 and Tuesday 15 September 2026 (UTC), so the
# span holds 48 half-hours of each. Monday's four population half-hours (-3 at
# 00:00, 06:00 and 12:00, 0 at 18:00) and Tuesday's one (+5 at 23:30, 00:30
# on Wednesday in London) weigh 48 / 4 against 48 / 1: S x P / N gives 48 and
# 192, total 384. In ascending order the cumulative weight reaches 192, half,
# at the 0, so the weighted median is the mean of 0 and +5, 2.5. Weighted
# positive share 192 / 336 = 4 / 7 (counting the 0's weight it would be 1 / 2):
# supported. Unweighted: median -3, positive share 1 / 4: contradicted.
Q1_EXACT_HALF_ROWS = """
insert into synthetic_drifts values
    ('2026-09-14 00:00+00', -3, true, -3, true),
    ('2026-09-14 06:00+00', -3, true, -3, true),
    ('2026-09-14 12:00+00', -3, true, -3, true),
    ('2026-09-14 18:00+00', 0, true, 0, true),
    ('2026-09-15 23:30+00', 5, true, 5, true)"""
CONTRADICTED = "contradicted: downward drift"
SUPPORTED = "supported: upward drift"
Q1_EXACT_HALF_EXPECTED = {
    (): dict(
        complete_half_hours=5,
        first_half_hour="2026-09-14 00:00:00+00:00",
        last_half_hour="2026-09-15 23:30:00+00:00",
        half_hours_needed=1,
        population_is_reached=True,
        reported_verdict=CONTRADICTED,
        median_drift=-3.0,
        positive_share="0.250000",
        as_published_half_hours=5,
        as_published_median_drift=-3.0,
        as_published_positive_share="0.250000",
        as_published_verdict=CONTRADICTED,
        weekday_reweighted_median_drift=2.5,
        weekday_reweighted_positive_share="0.571429",
        weekday_reweighted_verdict=SUPPORTED,
        part_not_seen_half_hours=5,
        part_not_seen_median_drift=-3.0,
        part_not_seen_positive_share="0.250000",
        part_not_seen_verdict=CONTRADICTED,
    )
}

# Every threshold on its boundary: 20 consecutive half-hours on Friday
# 11 September 2026 (one UTC weekday, so every weight is equal, and all after
# 2026-09-10 05:30 UTC). Drifts nine -1 and eleven +2: median 2, positive share
# 11 / 20 = 0.55, supported. As published nine +1 and eleven -2: median -2,
# share 9 / 20 = 0.45, contradicted. The two differ, so no direction is
# reported.
Q1_THRESHOLD_ROWS = """
insert into synthetic_drifts
select
    timestamptz '2026-09-11 00:00+00' + (number - 1) * interval '30 minutes',
    case when number <= 9 then -1 else 2 end,
    true,
    case when number <= 9 then 1 else -2 end,
    true
from generate_series(1, 20) as numbers (number)"""
Q1_THRESHOLD_EXPECTED = {
    (): dict(
        complete_half_hours=20,
        population_is_reached=True,
        reported_verdict="not robust to frozen polls: no directional verdict",
        median_drift=2.0,
        positive_share="0.550000",
        as_published_median_drift=-2.0,
        as_published_positive_share="0.450000",
        as_published_verdict=CONTRADICTED,
        weekday_reweighted_median_drift=2.0,
        weekday_reweighted_positive_share="0.550000",
        weekday_reweighted_verdict=SUPPORTED,
        part_not_seen_half_hours=20,
        part_not_seen_median_drift=2.0,
        part_not_seen_positive_share="0.550000",
        part_not_seen_verdict=SUPPORTED,
    )
}
# The same rows with two slot polls after the cutoff: only counts.
Q1_VERDICT_GATED = (
    "median_drift",
    "positive_share",
    "as_published_half_hours",
    "as_published_median_drift",
    "as_published_positive_share",
    "as_published_verdict",
    "weekday_reweighted_median_drift",
    "weekday_reweighted_positive_share",
    "weekday_reweighted_verdict",
    "part_not_seen_median_drift",
    "part_not_seen_positive_share",
    "part_not_seen_verdict",
)
Q1_THRESHOLD_NOT_REACHED_EXPECTED = {
    (): dict(
        complete_half_hours=20,
        population_is_reached=False,
        reported_verdict="population not reached: no result",
        part_not_seen_half_hours=20,
    )
}

# The part not seen starts after 2026-09-10 05:30 UTC, the project's value:
# -3 at 04:30, 05:00 and 05:30 (seen) and +3 at 06:00 and 06:30 (not seen).
# All five: median -3, share 2 / 5, contradicted. The part not seen: two, median
# 3, share 1, supported; the reported verdict stays the main one.
Q1_PART_NOT_SEEN_ROWS = """
insert into synthetic_drifts values
    ('2026-09-10 04:30+00', -3, true, -3, true),
    ('2026-09-10 05:00+00', -3, true, -3, true),
    ('2026-09-10 05:30+00', -3, true, -3, true),
    ('2026-09-10 06:00+00', 3, true, 3, true),
    ('2026-09-10 06:30+00', 3, true, 3, true)"""
Q1_PART_NOT_SEEN_EXPECTED = {
    (): dict(
        complete_half_hours=5,
        reported_verdict=CONTRADICTED,
        median_drift=-3.0,
        positive_share="0.400000",
        weekday_reweighted_verdict=CONTRADICTED,
        part_not_seen_half_hours=2,
        part_not_seen_median_drift=3.0,
        part_not_seen_positive_share="1.000000",
        part_not_seen_verdict=SUPPORTED,
    )
}


def q1_verdict_fixtures(drift_rows, gate_open=True):
    return Q1_VERDICT_FIXTURES.format(
        drift_rows=drift_rows,
        look_ahead_polls=project_values_look_ahead(gate_open),
    )


# Q1's beside analysis on half-hours given as (period_start UTC, drift, polls),
# each poll (horizon seconds, forecast, counted). Every poll is the first of
# its slot; the look-ahead polls follow them.
Q1_BESIDE = "q1_forecast_drift_beside"
Q1_SEED = "q1-2026-10-31"
Q1_BESIDE_FIXTURES = """
create temporary table synthetic_beside_drifts (
    period_start timestamptz,
    drift integer,
    has_complete_trajectory boolean
) on commit drop;
insert into synthetic_beside_drifts values {drift_rows};
create temporary table synthetic_beside_revisions (
    period_start timestamptz,
    horizon_seconds numeric,
    forecast integer,
    is_counted_poll boolean,
    is_first_poll_in_slot boolean,
    poll_slot timestamptz
) on commit drop;
insert into synthetic_beside_revisions values {poll_rows};
"""
Q1_BESIDE_TABLES = {
    "int_carbon_error_by_period": "pg_temp.synthetic_beside_drifts",
    "int_carbon_forecast__revisions": "pg_temp.synthetic_beside_revisions",
}


def q1_beside_fixtures(half_hours, gate_open=True):
    drift_rows = []
    poll_rows = []
    for period_start, drift, polls in half_hours:
        drift_rows.append(f"('{period_start}', {drift}, true)")
        for horizon_seconds, forecast, is_counted in polls:
            poll_rows.append(
                f"('{period_start}', {horizon_seconds}, {forecast}, "
                f"{str(is_counted).lower()}, true, null)"
            )
    polls = PROJECT_VALUES_LOOK_AHEAD
    if not gate_open:
        polls = PROJECT_VALUES_LOOK_AHEAD[:-1]
    for poll_slot, is_first in polls:
        poll_rows.append(
            f"(null, null, null, false, {str(is_first).lower()}, '{poll_slot}')"
        )
    return Q1_BESIDE_FIXTURES.format(
        drift_rows=", ".join(drift_rows), poll_rows=",\n    ".join(poll_rows)
    )


def one_change_half_hour(period_start, drift):
    """A half-hour whose two counted polls differ by its drift: one change, no pair."""

    return (period_start, drift, ((3600, 100, True), (1800, 100 + drift, True)))


# Forecasts 100, 101, 100, 101, 100 in descending horizon: changes +1, -1, +1,
# -1, pairs (1, -1), (-1, 1), (1, -1), r = -1 exactly. Its drift is 0, and it
# reads the same reversed.
Q1_ALTERNATING = (
    (
        "2026-09-20 12:00+00",
        0,
        (
            (7200, 100, True),
            (5400, 101, True),
            (3600, 100, True),
            (1800, 101, True),
            (0, 100, True),
        ),
    ),
)
# Two half-hours on one UTC date, 12 September 2026; the second starts at
# 23:30 UTC, 13 September in London. The first: 100, 102, 102, 101 (changes
# +2, 0, -1, drift +1), then a poll inside the half-hour, never used. The
# second: 200, 200, 203, 205 (changes 0, +3, +2, drift +5), with a first poll
# of its slot that is not counted between them. Pairs (2, 0), (0, -1), (0, 3),
# (3, 2): n 4, sums x 5, y 4, xy 6, x^2 13, y^2 14, so r = (4 x 6 - 5 x 4) /
# sqrt((4 x 13 - 25)(4 x 14 - 16)) = 4 / sqrt(1080) = 0.121716. Without the
# zeros the pairs are (2, -1) and (3, 2): r = 1. Two of six changes are 0. One
# date, so every draw repeats the population.
Q1_ZEROS_KEPT = (
    (
        "2026-09-12 10:00+00",
        1,
        (
            (7200, 100, True),
            (5400, 102, True),
            (3600, 102, True),
            (1800, 101, True),
            (-1, 90, True),
        ),
    ),
    (
        "2026-09-12 23:30+00",
        5,
        (
            (7200, 200, True),
            (5400, 200, True),
            (4500, 250, False),
            (3600, 203, True),
            (1800, 205, True),
        ),
    ),
)
Q1_ZEROS_KEPT_PAIRS = ((2, 0), (0, -1), (0, 3), (3, 2))
# Two dates holding -2 and 4 in equal numbers: every draw's mean is -2, 1 or
# 4, and each end holds over 5% of the draws (238 and 247 with this seed), so
# the interval is exactly -2 to 4. No half-hour has two changes, so there is
# no pair and no r in any draw.
Q1_TWO_DATES = (
    one_change_half_hour("2026-09-21 10:00+00", -2),
    one_change_half_hour("2026-09-21 11:00+00", -2),
    one_change_half_hour("2026-09-22 10:00+00", 4),
    one_change_half_hour("2026-09-22 11:00+00", 4),
)
# Three dates, the last with two half-hours: the interval comes from the
# independent model of the picks and of percentile_cont below.
Q1_THREE_DATES = (
    one_change_half_hour("2026-09-23 12:00+00", 0),
    one_change_half_hour("2026-09-24 12:00+00", 3),
    one_change_half_hour("2026-09-25 12:00+00", 6),
    one_change_half_hour("2026-09-25 13:00+00", 9),
)


def twelve_dates():
    """Twelve dates from 26 September 2026, one half-hour each, drifts 1, 2,
    4, ... 2048: almost every draw has its own mean, so the interval moves
    with any change to the picks."""

    half_hours = []
    for number in range(12):
        utc_date = date(2026, 9, 26) + timedelta(days=number)
        half_hours.append(one_change_half_hour(f"{utc_date} 12:00+00", 2**number))
    return tuple(half_hours)


Q1_TWELVE_DATES = twelve_dates()

POPULATION_ROW = (1, "population: complete half-hours (1 needed)", None)
UTC_DATES_ROW = (1, "UTC dates of the population", None)
READ_POSSIBLE_ROW = (1, "read possible", None)
MEAN_ROW = (2, "mean drift, gCO2/kWh", None)
R_ROW = (3, "Pearson's r of consecutive changes within a half-hour", None)
PAIRS_ROW = (3, "pairs of consecutive changes within a half-hour", None)
CHANGES_ROW = (3, "changes between consecutive counted polls", None)
ZERO_SHARE_ROW = (3, "share of changes equal to 0", None)
SUM_CHECK_ROW = (
    3,
    "population half-hours whose changes do not sum to their drift (expected 0)",
    None,
)
READING_ROW = (4, "reading", None)
MEAN_LABEL = (
    "not called pre-registered: an exploration on 11 September 2026 computed a "
    "near preview"
)
R_LABEL = (
    "not called pre-registered: a published analysis (gridcast) reports "
    "negative values on overlapping dates"
)
INTERVAL_UNAVAILABLE = (
    "; interval unavailable: fewer than 900 of 1000 draws have a value"
)
READING = (
    "Q1 shows the usual direction of change, not whether the forecast is efficient."
)


def interval_cells(value, low, high, draws_with_value=1000):
    return dict(
        value=value,
        interval_low=low,
        interval_high=high,
        draws_with_value=draws_with_value,
    )


Q1_ALTERNATING_EXPECTED = {
    POPULATION_ROW: dict(n=1),
    UTC_DATES_ROW: dict(n=1),
    READ_POSSIBLE_ROW: dict(detail="yes"),
    MEAN_ROW: dict(
        n=1, detail=MEAN_LABEL, **interval_cells("0.000000", "0.000000", "0.000000")
    ),
    R_ROW: dict(
        n=3, detail=R_LABEL, **interval_cells("-1.000000", "-1.000000", "-1.000000")
    ),
    PAIRS_ROW: dict(n=3),
    CHANGES_ROW: dict(n=4),
    ZERO_SHARE_ROW: dict(n=4, value="0.000000"),
    SUM_CHECK_ROW: dict(n=0),
    READING_ROW: dict(detail=READING),
}
Q1_ZEROS_KEPT_EXPECTED = {
    POPULATION_ROW: dict(n=2),
    UTC_DATES_ROW: dict(n=1),
    MEAN_ROW: dict(n=2, **interval_cells("3.000000", "3.000000", "3.000000")),
    R_ROW: dict(n=4, **interval_cells("0.121716", "0.121716", "0.121716")),
    PAIRS_ROW: dict(n=4),
    CHANGES_ROW: dict(n=6),
    ZERO_SHARE_ROW: dict(n=6, value="0.333333"),
    SUM_CHECK_ROW: dict(n=0),
}
Q1_TWO_DATES_EXPECTED = {
    POPULATION_ROW: dict(n=4),
    UTC_DATES_ROW: dict(n=2),
    MEAN_ROW: dict(n=4, **interval_cells("1.000000", "-2.000000", "4.000000")),
    R_ROW: dict(
        n=0,
        detail=R_LABEL + INTERVAL_UNAVAILABLE,
        **interval_cells(None, None, None, 0),
    ),
    PAIRS_ROW: dict(n=0),
    CHANGES_ROW: dict(n=4),
    ZERO_SHARE_ROW: dict(n=4, value="0.000000"),
    SUM_CHECK_ROW: dict(n=0),
}
Q1_TWO_DATES_EARLY_EXPECTED = {
    POPULATION_ROW: dict(n=4),
    UTC_DATES_ROW: dict(n=2),
    READ_POSSIBLE_ROW: dict(detail="no"),
    MEAN_ROW: dict(n=4, detail=MEAN_LABEL),
    R_ROW: dict(n=0, detail=R_LABEL),
    PAIRS_ROW: dict(n=0),
    CHANGES_ROW: dict(n=4),
    ZERO_SHARE_ROW: dict(n=4),
    SUM_CHECK_ROW: dict(n=0),
}
Q1_BESIDE_GATED = (
    {1, 2, 3, 4},
    ("value", "interval_low", "interval_high", "draws_with_value"),
)


def drift_values_by_date(half_hours):
    """Each UTC date's drifts, dates in ascending order."""

    by_date = {}
    for period_start, drift, _polls in half_hours:
        utc_date = period_start[:10]
        if utc_date not in by_date:
            by_date[utc_date] = []
        by_date[utc_date].append(drift)
    values = []
    for utc_date in sorted(by_date):
        values.append(by_date[utc_date])
    return values


def q1_model_mean_expected(half_hours):
    """The mean row with its interval from the independent model."""

    date_values = drift_values_by_date(half_hours)
    draws = draw_statistics(date_values, Q1_SEED, mean_of)
    population_values = []
    for values in date_values:
        population_values += values
    return {
        MEAN_ROW: dict(
            n=len(population_values),
            **interval_cells(
                six_decimals(mean_of(population_values)),
                six_decimals(percentile_cont(draws, Fraction(1, 20))),
                six_decimals(percentile_cont(draws, Fraction(19, 20))),
            ),
        )
    }


# Q10's rows: (period_start UTC, complete trajectory, error_24h, error_4h,
# error_final, error_api_final, and the exact horizons of the 24-hour, 4-hour
# and final picks in seconds). Every actual is 200. Local (Europe/London)
# dates in summer time are an hour ahead of UTC.
#
# On the common sample the 24-hour picks are 86,398.5 seconds ahead (the
# usual pick, polled 1.5 seconds after the half-hour), 88,198.5 (a fall-back),
# exactly 86,400.0 (not a fall-back), 86,400.1 (a fall-back) and 86,398.5:
# shortest 86,398.5, median 86,400, longest 88,198.5, two fall-backs. The
# 4-hour picks: 14,398.5 three times, 14,399 and 16,198.5 (one fall-back).
# The final picks: 1,798.5 four times and 0.1. Every pick is on the
# half-hour schedule.
Q10_ROWS = """
    ('2026-08-28 10:00+00', true, 10, -4, 2, 3, 86398.5, 14398.5, 1798.5),       -- Friday: weekday
    ('2026-08-30 23:30+00', true, -6, 3, -1, -2, 88198.5, 14398.5, 1798.5),      -- Monday 31 August locally: bank holiday (Sunday in UTC)
    ('2026-08-29 12:00+00', true, 4, 4, 4, null, 86400.0, 14399, 0.1),           -- Saturday: weekend
    ('2026-08-28 23:30+00', true, 2, -2, 0, 1, 86400.1, 16198.5, 1798.5),        -- Saturday locally: weekend (Friday in UTC)
    ('2026-08-28 11:00+00', true, null, 5, 1, 50, null, 14398.5, 1798.5),        -- no 24-hour forecast: dropped
    ('2026-08-28 12:00+00', true, 3, null, null, 50, 86398.5, null, null),       -- no 4-hour or final forecast: dropped
    ('2026-08-28 13:00+00', false, 100, 100, 100, 50, 86398.5, 14398.5, 1798.5), -- incomplete trajectory: not in Q1's population
    ('2026-08-16 12:00+00', true, 100, 100, 100, 50, 86398.5, 14398.5, 1798.5),  -- before the population starts
    ('2026-09-05 00:30+00', true, 100, 100, 100, 50, 86398.5, 14398.5, 1798.5),  -- after analysis_read_until
    ('2026-09-05 00:00+00', true, 0, 8, -3, -6, 86398.5, 14398.5, 1798.5)        -- at analysis_read_until; Saturday: weekend
"""

# The outturn of the reference half-hours (UTC); each target's actual is 200.
# Persistence reads the half-hour that ended at or before the pick's poll:
# the 12:00 target on 29 August has its 24-hour pick polled at exactly 12:00:00
# on the 28th (half-hour 11:30) and its final pick at 11:59:59.9 (half-hour
# 11:00). The day before is the half-hour 24 hours earlier.
#   persistence, 24 hours: 10, +4, -5, +1 (the 86,400.1 pick's half-hour, 22:30
#     on 27 August, has no outturn and is left out): MAE 20 / 4, bias 10 / 4
#   persistence, 4 hours: 6, -3, 8, -2, 0: MAE 19 / 5, bias 9 / 5
#   persistence, half an hour: 2, 3, 0, -1, -4: MAE 10 / 5, bias 0
#   day before: 5, 0, 7, -10 (5 September's half-hour 24 hours earlier has
#     none): MAE 22 / 4, bias 2 / 4
Q10_OUTTURN_ROWS = """
insert into synthetic_q10_outturn values
    ('2026-08-27 09:30+00', 190), ('2026-08-29 22:30+00', 205),
    ('2026-08-28 11:30+00', 196), ('2026-09-03 23:30+00', 199),
    ('2026-08-28 05:30+00', 194), ('2026-08-30 19:00+00', 202),
    ('2026-08-29 07:30+00', 203), ('2026-08-28 18:30+00', 192),
    ('2026-09-04 19:30+00', 200),
    ('2026-08-28 09:00+00', 198), ('2026-08-30 22:30+00', 201),
    ('2026-08-29 11:00+00', 197), ('2026-08-28 22:30+00', 200),
    ('2026-09-04 23:00+00', 204),
    ('2026-08-27 10:00+00', 195), ('2026-08-29 23:30+00', 210),
    ('2026-08-28 12:00+00', 200), ('2026-08-27 23:30+00', 193);
"""

# Slot polls after analysis_read_until (5 September 00:00): three settle a
# stall that runs past the cutoff, so the population can be reached; with
# two, it cannot. The poll in the cutoff's own slot and a later poll in the
# same slot do not count.
Q10_LOOK_AHEAD_POLLS = """
    ('2026-09-05 00:00+00', true),
    ('2026-09-05 00:30+00', true),
    ('2026-09-05 00:30+00', false),
    ('2026-09-05 01:30+00', true){third_poll}
"""
THIRD_POLL_AFTER_CUTOFF = ",\n    ('2026-09-05 02:00+00', true)"

Q10_FIXTURES = """
create temporary table synthetic_carbon_error (
    period_start timestamptz,
    has_complete_trajectory boolean,
    error_24h numeric,
    error_4h numeric,
    error_final numeric,
    error_api_final numeric,
    horizon_24h_seconds numeric,
    horizon_4h_seconds numeric,
    final_horizon_seconds numeric,
    actual numeric default 200
) on commit drop;
insert into synthetic_carbon_error (
    period_start, has_complete_trajectory, error_24h, error_4h, error_final,
    error_api_final, horizon_24h_seconds, horizon_4h_seconds, final_horizon_seconds
) values {error_rows};
create temporary table synthetic_q10_outturn (
    period_start timestamptz,
    actual numeric
) on commit drop;
{outturn_rows}
create temporary table synthetic_slot_polls (
    poll_slot timestamptz,
    is_first_poll_in_slot boolean
) on commit drop;
insert into synthetic_slot_polls values {look_ahead_polls};
"""


def q10_fixtures(
    third_poll=THIRD_POLL_AFTER_CUTOFF,
    error_rows=Q10_ROWS,
    outturn_rows=Q10_OUTTURN_ROWS,
):
    polls = Q10_LOOK_AHEAD_POLLS.format(third_poll=third_poll)
    return Q10_FIXTURES.format(
        error_rows=error_rows, outturn_rows=outturn_rows, look_ahead_polls=polls
    )


# Twelve settlement dates, 18 to 29 August 2026, one common-sample half-hour
# each at 12:00 UTC: 24-hour errors 1, 2, 4, ... 2048 (MAE and bias 4,095 /
# 12), 4-hour errors 0 to 11 (66 / 12) and final errors 0, -1, 2, -3, ... -11
# (MAE 66 / 12, bias -6 / 12). Almost every draw has its own 24-hour figures,
# so the intervals move with any change to the picks.
def q10_twelve_dates_rows():
    rows = []
    for number in range(12):
        period_start = date(2026, 8, 18) + timedelta(days=number)
        final_error = number if number % 2 == 0 else -number
        rows.append(
            f"('{period_start} 12:00+00', true, {2**number}, {number}, {final_error}, "
            "null, 86398.5, 14398.5, 1798.5)"
        )
    return ",\n    ".join(rows)


# On the project's own boundaries, cutoff 2 November 2026: 05:30 on 10
# September is seen and 06:00 is not; the 24-hour pick polled at 10:30:00.7
# that day ran on the half-hour schedule and the one at 11:05 on :05 and :35;
# the target at 12:00 UTC on 25 October has its day-before half-hour at 12:00
# UTC on the 24th (13:00 BST; 12:00 BST, 11:00 UTC, holds another value).
Q10_PROJECT_VALUES_ROWS = """
    ('2026-09-10 05:30+00', true, 1, 1, 1, 1, 86398.5, 14398.5, 1798.5),
    ('2026-09-10 06:00+00', true, 3, -5, 2, 2, 86398.5, 14398.5, 1798.5),
    ('2026-09-11 10:30+00', true, -1, 4, 0, 0, 86399.3, 14100, 1500),
    ('2026-09-11 11:00+00', true, 5, 0, -2, 3, 86100, 14100, 1500),
    ('2026-10-25 12:00+00', true, 2, 2, 2, 4, 86100, 14100, 1500)
"""
Q10_PROJECT_VALUES_OUTTURN_ROWS = """
insert into synthetic_q10_outturn values
    ('2026-10-24 12:00+00', 190), ('2026-10-24 11:00+00', 230);
"""


def q10_project_values_fixtures(gate_open=True):
    return Q10_FIXTURES.format(
        error_rows=Q10_PROJECT_VALUES_ROWS,
        outturn_rows=Q10_PROJECT_VALUES_OUTTURN_ROWS,
        look_ahead_polls=project_values_look_ahead(gate_open),
    )


Q10_TABLES = {
    "int_carbon_error_by_period": "pg_temp.synthetic_carbon_error",
    "int_carbon_outturn__latest": "pg_temp.synthetic_q10_outturn",
    "int_carbon_forecast__revisions": "pg_temp.synthetic_slot_polls",
}

FIRST = "2026-08-28 10:00:00+00:00"
LAST = "2026-09-05 00:00:00+00:00"
Q1_POPULATION = (1, "Q1 one-look half-hours (7 needed)", None)
ACCURACY = "accuracy on the common sample"
ABOUT_24_HOURS = "about 24 hours"
ABOUT_4_HOURS = "about 4 hours"
ABOUT_HALF_AN_HOUR = "about half an hour"
LEAD_TIMES = (ABOUT_24_HOURS, ABOUT_4_HOURS, ABOUT_HALF_AN_HOUR)
WEEKDAY = "settlement date: weekday"
WEEKEND = "settlement date: weekend"
BANK_HOLIDAY = "settlement date: bank holiday"
FALL_BACKS = "fall-back picks: made more than the lead time ahead"
SCHEDULE = "picks made on the capture schedule: {}"
PERSISTENCE = (
    "reference, persistence: outturn of the latest half-hour ended at or "
    "before the pick's poll"
)
PERSISTENCE_LEFT_OUT = (
    "reference, persistence: left out, no outturn for the reference half-hour"
)
DAY_BEFORE = "reference, the same half-hour the day before: outturn 24 hours earlier"
DAY_BEFORE_LEFT_OUT = (
    "reference, the same half-hour the day before: left out, no outturn for "
    "the reference half-hour"
)
API_FINAL = "consistency check: error_api_final on the common sample"
API_FINAL_LEFT_OUT = "consistency check: left out, no error_api_final"
PART_NOT_SEEN = "the part not seen"
REFERENCE_LABEL = "context, not a test; outturn as later stored"
DAY_BEFORE_LABEL = (
    "context, not a test; outturn as later stored; at the 24-hour lead time "
    "this half-hour ends 25 to 30 minutes after the forecast is made, 55 to 60 "
    "for a fall-back pick"
)
API_FINAL_LABEL = (
    "the API's stored final forecast: its timing is not documented, and its "
    "error on part of this period was seen before 31 October 2026"
)
PART_NOT_SEEN_LABEL = (
    "the part not seen: half-hours after 10 September 2026 05:30 UTC; it never "
    "changes the result"
)
Q10_SEED = "q10-2026-10-31"
Q10_INTERVAL_UNAVAILABLE = (
    "interval unavailable: fewer than 900 of 1000 draws have a value"
)

# (section, item, horizon) -> {column: expected}. The common sample is the
# five rows with all three errors: 24 hours 10, -6, 4, 2, 0; 4 hours
# -4, 3, 4, -2, 8; final 2, -1, 4, 0, -3.
Q10_OBSERVED_EXPECTED = {
    Q1_POPULATION: dict(
        half_hours=7,
        first_half_hour=FIRST,
        last_half_hour=LAST,
        population_is_reached=True,
    ),
    (1, "common sample: all three horizons", None): dict(
        half_hours=5, first_half_hour=FIRST, last_half_hour=LAST
    ),
    (1, "with a forecast at this horizon", ABOUT_24_HOURS): dict(half_hours=6),
    (1, "with a forecast at this horizon", ABOUT_4_HOURS): dict(half_hours=6),
    (1, "with a forecast at this horizon", ABOUT_HALF_AN_HOUR): dict(half_hours=6),
    (1, "dropped: no forecast at this horizon", ABOUT_24_HOURS): dict(
        half_hours=1,
        first_half_hour="2026-08-28 11:00:00+00:00",
        last_half_hour="2026-08-28 11:00:00+00:00",
    ),
    (1, "dropped: no forecast at this horizon", ABOUT_4_HOURS): dict(
        half_hours=1, first_half_hour="2026-08-28 12:00:00+00:00"
    ),
    (1, "dropped: no forecast at this horizon", ABOUT_HALF_AN_HOUR): dict(
        half_hours=1, first_half_hour="2026-08-28 12:00:00+00:00"
    ),
    # 88,198.5 and 86,400.1 seconds; 16,198.5; the final pick has none.
    (1, FALL_BACKS, ABOUT_24_HOURS): dict(half_hours=2, detail=None),
    (1, FALL_BACKS, ABOUT_4_HOURS): dict(half_hours=1, detail=None),
    (1, FALL_BACKS, ABOUT_HALF_AN_HOUR): dict(half_hours=None, detail="not applicable"),
    (1, SCHEDULE.format("on the half-hour"), ABOUT_24_HOURS): dict(half_hours=5),
    (1, SCHEDULE.format("on the half-hour"), ABOUT_4_HOURS): dict(half_hours=5),
    (1, SCHEDULE.format("on the half-hour"), ABOUT_HALF_AN_HOUR): dict(half_hours=5),
    (1, SCHEDULE.format(":05 and :35"), ABOUT_24_HOURS): dict(half_hours=0),
    (1, SCHEDULE.format(":05 and :35"), ABOUT_4_HOURS): dict(half_hours=0),
    (1, SCHEDULE.format(":05 and :35"), ABOUT_HALF_AN_HOUR): dict(half_hours=0),
    # 24 hours: 22 / 5 and 10 / 5; 4 hours: 21 / 5 and 9 / 5; final: 10 / 5 and 2 / 5.
    (2, ACCURACY, ABOUT_24_HOURS): dict(
        half_hours=5,
        mean_absolute_error="4.400",
        bias="2.000",
        shortest_horizon_seconds="86398.500",
        median_horizon_seconds="86400.000",
        longest_horizon_seconds="88198.500",
    ),
    (2, ACCURACY, ABOUT_4_HOURS): dict(
        half_hours=5,
        mean_absolute_error="4.200",
        bias="1.800",
        shortest_horizon_seconds="14398.500",
        median_horizon_seconds="14398.500",
        longest_horizon_seconds="16198.500",
    ),
    (2, ACCURACY, ABOUT_HALF_AN_HOUR): dict(
        half_hours=5,
        mean_absolute_error="2.000",
        bias="0.400",
        shortest_horizon_seconds="0.100",
        median_horizon_seconds="1798.500",
        longest_horizon_seconds="1798.500",
    ),
    # Weekday: the Friday 10:00 row only.
    (3, WEEKDAY, ABOUT_24_HOURS): dict(
        half_hours=1, mean_absolute_error="10.000", bias="10.000"
    ),
    (3, WEEKDAY, ABOUT_4_HOURS): dict(
        half_hours=1, mean_absolute_error="4.000", bias="-4.000"
    ),
    (3, WEEKDAY, ABOUT_HALF_AN_HOUR): dict(
        half_hours=1, mean_absolute_error="2.000", bias="2.000"
    ),
    # Weekend: 24 hours 4, 2, 0; 4 hours 4, -2, 8 (14 / 3, 10 / 3); final 4, 0, -3 (7 / 3, 1 / 3).
    (3, WEEKEND, ABOUT_24_HOURS): dict(
        half_hours=3, mean_absolute_error="2.000", bias="2.000"
    ),
    (3, WEEKEND, ABOUT_4_HOURS): dict(
        half_hours=3, mean_absolute_error="4.667", bias="3.333"
    ),
    (3, WEEKEND, ABOUT_HALF_AN_HOUR): dict(
        half_hours=3, mean_absolute_error="2.333", bias="0.333"
    ),
    (3, BANK_HOLIDAY, ABOUT_24_HOURS): dict(
        half_hours=1, mean_absolute_error="6.000", bias="-6.000"
    ),
    (3, BANK_HOLIDAY, ABOUT_4_HOURS): dict(
        half_hours=1, mean_absolute_error="3.000", bias="3.000"
    ),
    (3, BANK_HOLIDAY, ABOUT_HALF_AN_HOUR): dict(
        half_hours=1, mean_absolute_error="1.000", bias="-1.000"
    ),
    # The references and the check, as worked beside the outturn rows.
    (4, PERSISTENCE, ABOUT_24_HOURS): dict(
        half_hours=4, mean_absolute_error="5.000", bias="2.500"
    ),
    (4, PERSISTENCE, ABOUT_4_HOURS): dict(
        half_hours=5, mean_absolute_error="3.800", bias="1.800"
    ),
    (4, PERSISTENCE, ABOUT_HALF_AN_HOUR): dict(
        half_hours=5, mean_absolute_error="2.000", bias="0.000"
    ),
    (4, PERSISTENCE_LEFT_OUT, ABOUT_24_HOURS): dict(half_hours=1),
    (4, PERSISTENCE_LEFT_OUT, ABOUT_4_HOURS): dict(half_hours=0),
    (4, PERSISTENCE_LEFT_OUT, ABOUT_HALF_AN_HOUR): dict(half_hours=0),
    (4, DAY_BEFORE, None): dict(
        half_hours=4, mean_absolute_error="5.500", bias="0.500"
    ),
    (4, DAY_BEFORE_LEFT_OUT, None): dict(half_hours=1),
    # The API final on the common sample: 3, -2, 1, -6 (the 12:00 row on 29
    # August has none); the dropped rows' 50s are not on the sample.
    (5, API_FINAL, None): dict(
        half_hours=4, mean_absolute_error="3.000", bias="-1.000"
    ),
    (5, API_FINAL_LEFT_OUT, None): dict(half_hours=1),
    # Every common-sample half-hour is before 10 September 05:30 UTC.
    (6, PART_NOT_SEEN, ABOUT_24_HOURS): dict(
        half_hours=0, mean_absolute_error=None, bias=None
    ),
    (6, PART_NOT_SEEN, ABOUT_4_HOURS): dict(half_hours=0, mean_absolute_error=None),
    (6, PART_NOT_SEEN, ABOUT_HALF_AN_HOUR): dict(
        half_hours=0, mean_absolute_error=None
    ),
}

# Each bootstrap cell's errors by settlement date, for the independent model
# of the interval: 28 and 29 August (Friday and Saturday), 31 August (bank
# holiday) and 5 September (Saturday). The half-hour at 23:30 UTC on the 28th
# is on the 29th.
Q10_CELL_ERRORS = {
    (2, ACCURACY, ABOUT_24_HOURS): ([10], [4, 2], [-6], [0]),
    (2, ACCURACY, ABOUT_4_HOURS): ([-4], [4, -2], [3], [8]),
    (2, ACCURACY, ABOUT_HALF_AN_HOUR): ([2], [4, 0], [-1], [-3]),
    (3, WEEKDAY, ABOUT_24_HOURS): ([10], [], [], []),
    (3, WEEKDAY, ABOUT_4_HOURS): ([-4], [], [], []),
    (3, WEEKDAY, ABOUT_HALF_AN_HOUR): ([2], [], [], []),
    (3, WEEKEND, ABOUT_24_HOURS): ([], [4, 2], [], [0]),
    (3, WEEKEND, ABOUT_4_HOURS): ([], [4, -2], [], [8]),
    (3, WEEKEND, ABOUT_HALF_AN_HOUR): ([], [4, 0], [], [-3]),
    (3, BANK_HOLIDAY, ABOUT_24_HOURS): ([], [], [-6], []),
    (3, BANK_HOLIDAY, ABOUT_4_HOURS): ([], [], [3], []),
    (3, BANK_HOLIDAY, ABOUT_HALF_AN_HOUR): ([], [], [-1], []),
    (4, PERSISTENCE, ABOUT_24_HOURS): ([10], [4], [-5], [1]),
    (4, PERSISTENCE, ABOUT_4_HOURS): ([6], [-3, 8], [-2], [0]),
    (4, PERSISTENCE, ABOUT_HALF_AN_HOUR): ([2], [3, 0], [-1], [-4]),
    (4, DAY_BEFORE, None): ([5], [0, 7], [-10], []),
    (5, API_FINAL, None): ([3], [1], [-2], [-6]),
    (6, PART_NOT_SEEN, ABOUT_24_HOURS): ([], [], [], []),
    (6, PART_NOT_SEEN, ABOUT_4_HOURS): ([], [], [], []),
    (6, PART_NOT_SEEN, ABOUT_HALF_AN_HOUR): ([], [], [], []),
}
Q10_LABELS = {
    4: REFERENCE_LABEL,
    5: API_FINAL_LABEL,
    6: PART_NOT_SEEN_LABEL,
}
Q10_DAY_BEFORE_LABELS = {(4, DAY_BEFORE, None): DAY_BEFORE_LABEL}

Q10_TWELVE_DATES_EXPECTED = {
    (2, ACCURACY, ABOUT_24_HOURS): dict(
        half_hours=12, mean_absolute_error="341.250", bias="341.250"
    ),
    (2, ACCURACY, ABOUT_4_HOURS): dict(
        half_hours=12, mean_absolute_error="5.500", bias="5.500"
    ),
    (2, ACCURACY, ABOUT_HALF_AN_HOUR): dict(
        half_hours=12, mean_absolute_error="5.500", bias="-0.500"
    ),
}


def q10_twelve_dates_cell_errors():
    by_lead_time = {ABOUT_24_HOURS: [], ABOUT_4_HOURS: [], ABOUT_HALF_AN_HOUR: []}
    for number in range(12):
        final_error = number if number % 2 == 0 else -number
        by_lead_time[ABOUT_24_HOURS].append([2**number])
        by_lead_time[ABOUT_4_HOURS].append([number])
        by_lead_time[ABOUT_HALF_AN_HOUR].append([final_error])
    cell_errors = {}
    for lead_time, errors_by_date in by_lead_time.items():
        cell_errors[(2, ACCURACY, lead_time)] = tuple(errors_by_date)
    return cell_errors


# Project values: the part not seen holds 06:00 on 10 September, 11 September
# 10:30 and 11:00, and 25 October 12:00: 24 hours 3, -1, 5, 2 (MAE 11 / 4,
# bias 9 / 4); 4 hours -5, 4, 0, 2 (11 / 4, 1 / 4); final 2, 0, -2, 2 (6 / 4,
# 2 / 4). The 24-hour picks polled 10:30:00.7 and earlier ran on the
# half-hour (three), the others at :05 and :35 (two); the 4-hour and final
# picks of 10 September's two half-hours ran on the half-hour. 25 October's
# day before is 200 - 190.
Q10_PROJECT_VALUES_EXPECTED = {
    (1, SCHEDULE.format("on the half-hour"), ABOUT_24_HOURS): dict(half_hours=3),
    (1, SCHEDULE.format(":05 and :35"), ABOUT_24_HOURS): dict(half_hours=2),
    (1, SCHEDULE.format("on the half-hour"), ABOUT_4_HOURS): dict(half_hours=2),
    (1, SCHEDULE.format(":05 and :35"), ABOUT_4_HOURS): dict(half_hours=3),
    (1, SCHEDULE.format("on the half-hour"), ABOUT_HALF_AN_HOUR): dict(half_hours=2),
    (1, SCHEDULE.format(":05 and :35"), ABOUT_HALF_AN_HOUR): dict(half_hours=3),
    (1, FALL_BACKS, ABOUT_24_HOURS): dict(half_hours=0),
    (6, PART_NOT_SEEN, ABOUT_24_HOURS): dict(
        half_hours=4, mean_absolute_error="2.750", bias="2.250"
    ),
    (6, PART_NOT_SEEN, ABOUT_4_HOURS): dict(
        half_hours=4, mean_absolute_error="2.750", bias="0.250"
    ),
    (6, PART_NOT_SEEN, ABOUT_HALF_AN_HOUR): dict(
        half_hours=4, mean_absolute_error="1.500", bias="0.500"
    ),
    (4, DAY_BEFORE, None): dict(
        half_hours=1, mean_absolute_error="10.000", bias="10.000"
    ),
    (4, DAY_BEFORE_LEFT_OUT, None): dict(half_hours=4),
}

Q10_NOT_REACHED_EXPECTED = {
    (1, "Q1 one-look half-hours (8 needed)", None): dict(
        half_hours=7, population_is_reached=False
    ),
    (2, ACCURACY, ABOUT_24_HOURS): dict(
        half_hours=5, mean_absolute_error=None, bias=None
    ),
    (3, WEEKEND, ABOUT_4_HOURS): dict(
        half_hours=3, mean_absolute_error=None, bias=None
    ),
    (1, FALL_BACKS, ABOUT_24_HOURS): dict(half_hours=2),
    (1, SCHEDULE.format("on the half-hour"), ABOUT_4_HOURS): dict(half_hours=5),
    (4, PERSISTENCE, ABOUT_24_HOURS): dict(half_hours=4),
    (4, PERSISTENCE_LEFT_OUT, ABOUT_24_HOURS): dict(half_hours=1),
    (4, DAY_BEFORE_LEFT_OUT, None): dict(half_hours=1),
    (5, API_FINAL_LEFT_OUT, None): dict(half_hours=1),
}


def q10_interval_cells(errors_by_date, label=None):
    """A cell's interval columns and detail from the independent model.

    errors_by_date lists the cell's errors on each common-sample settlement
    date, dates in ascending order. A draw that picks none of them has no
    value; under 900 draws with a value the interval is unavailable.
    """

    absolute_errors = draw_statistics(errors_by_date, Q10_SEED, mean_absolute_of)
    errors = draw_statistics(errors_by_date, Q10_SEED, mean_of)
    draws_with_value = len(errors)
    notes = []
    if label is not None:
        notes.append(label)
    if draws_with_value < 900:
        notes.append(Q10_INTERVAL_UNAVAILABLE)
        return dict(
            draws_with_value=draws_with_value,
            mae_interval_low=None,
            mae_interval_high=None,
            bias_interval_low=None,
            bias_interval_high=None,
            detail="; ".join(notes) or None,
        )
    return dict(
        draws_with_value=draws_with_value,
        mae_interval_low=three_decimals(
            percentile_cont(absolute_errors, Fraction(1, 20))
        ),
        mae_interval_high=three_decimals(
            percentile_cont(absolute_errors, Fraction(19, 20))
        ),
        bias_interval_low=three_decimals(percentile_cont(errors, Fraction(1, 20))),
        bias_interval_high=three_decimals(percentile_cont(errors, Fraction(19, 20))),
        detail="; ".join(notes) or None,
    )


def with_model_intervals(observed, cell_errors, labels):
    """The observed expectations with each bootstrap cell's interval added."""

    expected = dict(observed)
    for cell, errors_by_date in cell_errors.items():
        section, _item, _horizon = cell
        label = labels.get(cell, Q10_LABELS.get(section))
        columns = dict(expected.get(cell, {}))
        columns.update(q10_interval_cells(errors_by_date, label))
        expected[cell] = columns
    return expected


# Before its population each analysis shows counts only: these columns are
# null in every row of these sections.
Q10_GATED = (
    {1, 2, 3, 4, 5, 6},
    (
        "mean_absolute_error",
        "bias",
        "mae_interval_low",
        "mae_interval_high",
        "bias_interval_low",
        "bias_interval_high",
        "draws_with_value",
        "shortest_horizon_seconds",
        "median_horizon_seconds",
        "longest_horizon_seconds",
    ),
)


# Q25: one acceptance that crosses 00:00 UTC, the boundary between two daily
# BOALF captures, on the night of 31 August. The first day's capture holds its
# three ramp points, 23:50 to 00:05; the second day's capture repeats only the
# point from 00:00. Taking the latest capture of each ramp point keeps all
# three: a 15-minute span. Taking the latest capture of the whole acceptance
# would keep the last point alone: 5 minutes, under 10. Both captures fall on
# settlement date 1 September (London time), so the month is September.
# Acceptance 8 is the same in winter, at the end of January, when UTC and
# London time agree: its points fall on settlement dates 31 January and 1
# February, and its month is that of its first ramp point, January.
Q25_FIXTURES = """
create temporary table synthetic_boalf (
    national_grid_bm_unit text,
    acceptance_number integer,
    acceptance_time timestamptz,
    settlement_date date,
    time_from timestamptz,
    time_to timestamptz,
    retrieved_at timestamptz
) on commit drop;
insert into synthetic_boalf values
    ('STOR-1', 7, '2026-08-31 23:45+00', '2026-09-01',
        '2026-08-31 23:50+00', '2026-08-31 23:55+00', '2026-09-01 01:00+00'),
    ('STOR-1', 7, '2026-08-31 23:45+00', '2026-09-01',
        '2026-08-31 23:55+00', '2026-09-01 00:00+00', '2026-09-01 01:00+00'),
    ('STOR-1', 7, '2026-08-31 23:45+00', '2026-09-01',
        '2026-09-01 00:00+00', '2026-09-01 00:05+00', '2026-09-01 01:00+00'),
    ('STOR-1', 7, '2026-08-31 23:45+00', '2026-09-01',
        '2026-09-01 00:00+00', '2026-09-01 00:05+00', '2026-09-02 01:00+00'),
    ('STOR-1', 8, '2026-01-31 23:45+00', '2026-01-31',
        '2026-01-31 23:50+00', '2026-01-31 23:55+00', '2026-02-01 01:00+00'),
    ('STOR-1', 8, '2026-01-31 23:45+00', '2026-01-31',
        '2026-01-31 23:55+00', '2026-02-01 00:00+00', '2026-02-01 01:00+00'),
    ('STOR-1', 8, '2026-01-31 23:45+00', '2026-02-01',
        '2026-02-01 00:00+00', '2026-02-01 00:05+00', '2026-02-01 01:00+00'),
    ('STOR-1', 8, '2026-01-31 23:45+00', '2026-02-01',
        '2026-02-01 00:00+00', '2026-02-01 00:05+00', '2026-02-02 01:00+00');
create temporary table synthetic_cohort (
    national_grid_bm_unit text,
    fuel_group text
) on commit drop;
insert into synthetic_cohort values ('STOR-1', 'other');
"""

Q25_TABLES = {
    "stg_elexon__boalf": "pg_temp.synthetic_boalf",
    "int_elexon__bm_unit_cohort": "pg_temp.synthetic_cohort",
}
Q25_KEY = ("section", "fuel_group", "settlement_month", "measure")
GRANULARITY = "1 dispatch granularity (acceptances)"

Q25_EXPECTED = {
    (1, "other", "2026-01-01", GRANULARITY): dict(
        n=1, under_10_minutes=0, median_minutes="15.0"
    ),
    (1, "other", "2026-09-01", GRANULARITY): dict(
        n=1, under_10_minutes=0, median_minutes="15.0"
    ),
    (2, "other", None, GRANULARITY): dict(
        n=2, under_10_minutes=0, median_minutes="15.0"
    ),
    # Both acceptances span two captures; one ramp point of each, the one
    # from 00:00, is captured twice.
    (3, None, None, "acceptances spanning more than one capture"): dict(n=2),
    (3, None, None, "ramp points captured more than once"): dict(n=2),
}


# Q37: eleven half-hours (UTC; London is an hour ahead, so 23:00 on 31 August
# is settlement date 1 September). Every level is flat over its half-hour
# unless said, so a difference of d MW from the PN is d / 2 MWh. WIND-1 plans
# 2,000 MW (1,000 MWh) and WIND-2 500 MW (250 MWh) wherever they have a PN.
#
# 21:00 on 31 August: WIND-1 has a PN and a placeholder stretch only; no
#   stretch yet, so it is before the first half-hour read.
# 22:00 (August): WIND-1 flagged to 1,900: 50 down, exactly 100 MW. Its ramp
#   point's earlier capture said 1,000 and its PN's earlier capture 3,000;
#   neither is used. GAS-1 from 100 to 300, not flagged: 100 up.
# 23:00 (September): WIND-1 flagged to 1,500: 250 down, exactly 500 MW.
#   WIND-2 to 400, not flagged: 50 down that does not count towards the band.
#   GAS-1 flagged from 100 to 500: 200 up. BATT-1, with no fuel type, from
#   -50 to 10 for 23:20 to 23:30 only, not flagged: 60 MW for a sixth of
#   an hour, 10 up; the PN segment that ends at 23:10 adds nothing.
# 10:00 on 1 September: WIND-1 flagged to 1,000: 500 down, exactly 1 GW.
#   GAS-1 from 400 to 300, not flagged: 50 down. WIND-2 accepted at its
#   PN, 500: no energy either way. PS-1 has a stretch (5 MWh, not
#   flagged) and no PN: left out, and the half-hour keeps its band
#   because the stretch is not wind's.
# 11:00: WIND-1 flagged to 0: 1,000 down, exactly 2 GW. PS-1 flagged from 0
#   to 120 for 11:00 to 11:10 only: 20 up; its PN segment from 11:20, at
#   60, adds nothing. HYDRO-1 plans 0, in two PN segments that meet at
#   11:15, and its ramp point climbs from 0 to 300 over the half-hour,
#   but its stretch is 11:10 to 11:25 only, off the ramp's centre: the
#   level is 100, 150 and 250 at 11:10, 11:15 and 11:25, so 125 MW for
#   a twelfth of an hour then 200 MW for a sixth, 43.75 up. BIO-1 is
#   held at 0 for 11:10 to 11:25 while its PN climbs from 0 to 300 over
#   the half-hour: the PN is 100 at 11:10 and 250 at 11:25, 175 MW on
#   average for a quarter of an hour, 43.75 down.
# 12:00: WIND-1 flagged to 1,999 until 12:15: 0.25 down; then, not flagged,
#   to 2,040: 10 up. A second ramp point of that acceptance, from 12:00 at
#   2,100, also spans the later stretch; the one that starts latest is
#   used. GAS-1 plans 100 and is ramped
#   from 0 to 400, not flagged: the lines cross a quarter of the way in, so
#   0.5 x 100 x 100 / (2 x 400) = 6.25 down and 0.5 x 300 x 300 / (2 x 400)
#   = 56.25 up.
# 13:00: WIND-2 to 300, not flagged: 100 down, band none. GAS-1 held at 160
#   while its PN steps from 100 to 200 at 13:15: 15 up, then 10 down; its
#   stored energy says 81 where its level gives 80.
# 14:00: WIND-1's PN capture is incomplete and WIND-2 has none, so the
#   half-hour is not read: WIND-1's stretch (700 MWh at its level) is left
#   out and GAS-1's 50 up, from 100 to 200, is counted beside the result.
# 15:00: WIND-1's ramp points run to 15:10 and from 15:20 but its stretch
#   is 15:00 to 15:20, so the stretch (900 MWh) is left out, though an
#   earlier capture of the first ramp point ran to 15:30; the half-hour
#   is read, in the band for wind turn-down not measured. Its next
#   stretch, 15:20 to 15:30 to 1,940 and not flagged, is measured: 60 MW
#   for a sixth of an hour, 10 down. GAS-1 from 100 to 130, not flagged:
#   15 up.
# 00:00 on 5 September, the cutoff: WIND-1 has a PN and no stretch; GAS-1
#   from 100 to 140 until 00:15, with no flag value: 10 up, counted as not
#   flagged; then flagged to 180: 20 up. 00:30: WIND-1 flagged to 0, after
#   the cutoff.
#
# August: 1 half-hour, 50 flagged of 1,000 planned, 5.00%. September: 7
# half-hours (23:00, 10:00 to 13:00, 15:00, 00:00), 4 with a flagged
# turn-down, 3 at 100 MW or more, 2 at 1 GW or more, 1 not measured; 300 +
# 500 + 1,000 + 0.25 + 100 + 10 = 1,910.25 down, 1,750.25 of it flagged; 4 x
# 1,000 + 3 x 1,250 = 7,750 planned; 1,750.25 / 7,750 = 22.58%. All months:
# 1,800.25 / 8,750 = 20.57%.
Q37 = "q37_wind_turn_down_and_turn_up"
Q37_FIXTURES = """
create temporary table synthetic_intervals (
    national_grid_bm_unit text,
    period_start_utc timestamptz,
    interval_from timestamptz,
    interval_to timestamptz,
    acceptance_number integer,
    so_flag boolean,
    instructed_mwh numeric
) on commit drop;
insert into synthetic_intervals values
    ('WIND-1', '2026-08-31 21:00+00', null, null, null, null, null),
    ('WIND-1', '2026-08-31 22:00+00',
        '2026-08-31 22:00+00', '2026-08-31 22:30+00', 1, true, 950),
    ('GAS-1', '2026-08-31 22:00+00',
        '2026-08-31 22:00+00', '2026-08-31 22:30+00', 2, false, 150),
    ('WIND-1', '2026-08-31 23:00+00',
        '2026-08-31 23:00+00', '2026-08-31 23:30+00', 3, true, 750),
    ('WIND-2', '2026-08-31 23:00+00',
        '2026-08-31 23:00+00', '2026-08-31 23:30+00', 4, false, 200),
    ('GAS-1', '2026-08-31 23:00+00',
        '2026-08-31 23:00+00', '2026-08-31 23:30+00', 5, true, 250),
    ('BATT-1', '2026-08-31 23:00+00',
        '2026-08-31 23:20+00', '2026-08-31 23:30+00', 6, false, 1.666667),
    ('WIND-1', '2026-09-01 10:00+00',
        '2026-09-01 10:00+00', '2026-09-01 10:30+00', 7, true, 500),
    ('GAS-1', '2026-09-01 10:00+00',
        '2026-09-01 10:00+00', '2026-09-01 10:30+00', 8, false, 150),
    ('WIND-2', '2026-09-01 10:00+00',
        '2026-09-01 10:00+00', '2026-09-01 10:30+00', 19, false, 250),
    ('PS-1', '2026-09-01 10:00+00',
        '2026-09-01 10:00+00', '2026-09-01 10:30+00', 23, false, 5),
    ('WIND-1', '2026-09-01 11:00+00',
        '2026-09-01 11:00+00', '2026-09-01 11:30+00', 9, true, 0),
    ('PS-1', '2026-09-01 11:00+00',
        '2026-09-01 11:00+00', '2026-09-01 11:10+00', 10, true, 20),
    ('HYDRO-1', '2026-09-01 11:00+00',
        '2026-09-01 11:10+00', '2026-09-01 11:25+00', 26, false, 43.75),
    ('BIO-1', '2026-09-01 11:00+00',
        '2026-09-01 11:10+00', '2026-09-01 11:25+00', 27, false, 0),
    ('WIND-1', '2026-09-01 12:00+00',
        '2026-09-01 12:00+00', '2026-09-01 12:15+00', 11, true, 499.75),
    ('WIND-1', '2026-09-01 12:00+00',
        '2026-09-01 12:15+00', '2026-09-01 12:30+00', 21, false, 510),
    ('GAS-1', '2026-09-01 12:00+00',
        '2026-09-01 12:00+00', '2026-09-01 12:30+00', 12, false, 100),
    ('WIND-2', '2026-09-01 13:00+00',
        '2026-09-01 13:00+00', '2026-09-01 13:30+00', 13, false, 150),
    ('GAS-1', '2026-09-01 13:00+00',
        '2026-09-01 13:00+00', '2026-09-01 13:30+00', 14, false, 81),
    ('GAS-1', '2026-09-01 14:00+00',
        '2026-09-01 14:00+00', '2026-09-01 14:30+00', 15, false, 100),
    ('WIND-1', '2026-09-01 14:00+00',
        '2026-09-01 14:00+00', '2026-09-01 14:30+00', 16, true, 700),
    ('WIND-1', '2026-09-01 15:00+00',
        '2026-09-01 15:00+00', '2026-09-01 15:20+00', 17, true, 900),
    ('WIND-1', '2026-09-01 15:00+00',
        '2026-09-01 15:20+00', '2026-09-01 15:30+00', 25, false, 323.333333),
    ('GAS-1', '2026-09-01 15:00+00',
        '2026-09-01 15:00+00', '2026-09-01 15:30+00', 22, false, 65),
    ('GAS-1', '2026-09-05 00:00+00',
        '2026-09-05 00:00+00', '2026-09-05 00:15+00', 20, null, 35),
    ('GAS-1', '2026-09-05 00:00+00',
        '2026-09-05 00:15+00', '2026-09-05 00:30+00', 24, true, 45),
    ('WIND-1', '2026-09-05 00:30+00',
        '2026-09-05 00:30+00', '2026-09-05 01:00+00', 18, true, 0);
create temporary table synthetic_cohort (
    national_grid_bm_unit text,
    fuel_group text
) on commit drop;
insert into synthetic_cohort values
    ('WIND-1', 'wind'),
    ('WIND-2', 'wind'),
    ('GAS-1', 'gas_turbine'),
    ('PS-1', 'pumped_storage'),
    ('HYDRO-1', 'hydro'),
    ('BIO-1', 'bioenergy');
create temporary table synthetic_boalf (
    national_grid_bm_unit text,
    acceptance_number integer,
    time_from timestamptz,
    time_to timestamptz,
    level_from numeric,
    level_to numeric,
    retrieved_at timestamptz
) on commit drop;
insert into synthetic_boalf values
    ('WIND-1', 1, '2026-08-31 22:00+00', '2026-08-31 22:30+00',
        1000, 1000, '2026-09-01 01:00+00'),
    ('WIND-1', 1, '2026-08-31 22:00+00', '2026-08-31 22:30+00',
        1900, 1900, '2026-09-02 01:00+00'),
    ('GAS-1', 2, '2026-08-31 22:00+00', '2026-08-31 22:30+00',
        300, 300, '2026-09-02 01:00+00'),
    ('WIND-1', 3, '2026-08-31 23:00+00', '2026-08-31 23:30+00',
        1500, 1500, '2026-09-02 01:00+00'),
    ('WIND-2', 4, '2026-08-31 23:00+00', '2026-08-31 23:30+00',
        400, 400, '2026-09-02 01:00+00'),
    ('GAS-1', 5, '2026-08-31 23:00+00', '2026-08-31 23:30+00',
        500, 500, '2026-09-02 01:00+00'),
    ('BATT-1', 6, '2026-08-31 23:20+00', '2026-08-31 23:30+00',
        10, 10, '2026-09-02 01:00+00'),
    ('WIND-1', 7, '2026-09-01 10:00+00', '2026-09-01 10:30+00',
        1000, 1000, '2026-09-02 01:00+00'),
    ('GAS-1', 8, '2026-09-01 10:00+00', '2026-09-01 10:30+00',
        300, 300, '2026-09-02 01:00+00'),
    ('WIND-1', 9, '2026-09-01 11:00+00', '2026-09-01 11:30+00',
        0, 0, '2026-09-02 01:00+00'),
    ('WIND-2', 19, '2026-09-01 10:00+00', '2026-09-01 10:30+00',
        500, 500, '2026-09-02 01:00+00'),
    ('PS-1', 23, '2026-09-01 10:00+00', '2026-09-01 10:30+00',
        10, 10, '2026-09-02 01:00+00'),
    ('PS-1', 10, '2026-09-01 11:00+00', '2026-09-01 11:10+00',
        120, 120, '2026-09-02 01:00+00'),
    ('HYDRO-1', 26, '2026-09-01 11:00+00', '2026-09-01 11:30+00',
        0, 300, '2026-09-02 01:00+00'),
    ('BIO-1', 27, '2026-09-01 11:10+00', '2026-09-01 11:25+00',
        0, 0, '2026-09-02 01:00+00'),
    ('WIND-1', 11, '2026-09-01 12:00+00', '2026-09-01 12:15+00',
        1999, 1999, '2026-09-02 01:00+00'),
    ('WIND-1', 21, '2026-09-01 12:00+00', '2026-09-01 12:30+00',
        2100, 2100, '2026-09-02 01:00+00'),
    ('WIND-1', 21, '2026-09-01 12:15+00', '2026-09-01 12:30+00',
        2040, 2040, '2026-09-02 01:00+00'),
    ('GAS-1', 12, '2026-09-01 12:00+00', '2026-09-01 12:30+00',
        0, 400, '2026-09-02 01:00+00'),
    ('WIND-2', 13, '2026-09-01 13:00+00', '2026-09-01 13:30+00',
        300, 300, '2026-09-02 01:00+00'),
    ('GAS-1', 14, '2026-09-01 13:00+00', '2026-09-01 13:30+00',
        160, 160, '2026-09-02 01:00+00'),
    ('GAS-1', 15, '2026-09-01 14:00+00', '2026-09-01 14:30+00',
        200, 200, '2026-09-02 01:00+00'),
    ('WIND-1', 16, '2026-09-01 14:00+00', '2026-09-01 14:30+00',
        1400, 1400, '2026-09-02 01:00+00'),
    ('WIND-1', 17, '2026-09-01 15:00+00', '2026-09-01 15:30+00',
        1800, 1800, '2026-09-01 20:00+00'),
    ('WIND-1', 17, '2026-09-01 15:00+00', '2026-09-01 15:10+00',
        1800, 1800, '2026-09-02 01:00+00'),
    ('WIND-1', 17, '2026-09-01 15:20+00', '2026-09-01 15:30+00',
        1700, 1700, '2026-09-02 01:00+00'),
    ('WIND-1', 25, '2026-09-01 15:20+00', '2026-09-01 15:30+00',
        1940, 1940, '2026-09-02 01:00+00'),
    ('GAS-1', 22, '2026-09-01 15:00+00', '2026-09-01 15:30+00',
        130, 130, '2026-09-02 01:00+00'),
    ('GAS-1', 20, '2026-09-05 00:00+00', '2026-09-05 00:15+00',
        140, 140, '2026-09-06 01:00+00'),
    ('GAS-1', 24, '2026-09-05 00:15+00', '2026-09-05 00:30+00',
        180, 180, '2026-09-06 01:00+00'),
    ('WIND-1', 18, '2026-09-05 00:30+00', '2026-09-05 01:00+00',
        0, 0, '2026-09-06 01:00+00');
create temporary table synthetic_commitments (
    national_grid_bm_unit text,
    settlement_date date,
    settlement_period integer,
    period_start_utc timestamptz,
    pn_mwh numeric,
    coverage_status text,
    pn_retrieved_at timestamptz
) on commit drop;
insert into synthetic_commitments values
    ('WIND-1', '2026-08-31', 45, '2026-08-31 21:00+00', 1000, 'complete', '2026-09-06 00:00+00'),
    ('WIND-1', '2026-08-31', 47, '2026-08-31 22:00+00', 1000, 'complete', '2026-09-06 00:00+00'),
    ('GAS-1', '2026-08-31', 47, '2026-08-31 22:00+00', 50, 'complete', '2026-09-06 00:00+00'),
    ('WIND-1', '2026-09-01', 1, '2026-08-31 23:00+00', 1000, 'complete', '2026-09-06 00:00+00'),
    ('WIND-2', '2026-09-01', 1, '2026-08-31 23:00+00', 250, 'complete', '2026-09-06 00:00+00'),
    ('GAS-1', '2026-09-01', 1, '2026-08-31 23:00+00', 50, 'complete', '2026-09-06 00:00+00'),
    ('BATT-1', '2026-09-01', 1, '2026-08-31 23:00+00', -25, 'complete', '2026-09-06 00:00+00'),
    ('WIND-1', '2026-09-01', 23, '2026-09-01 10:00+00', 1000, 'complete', '2026-09-06 00:00+00'),
    ('GAS-1', '2026-09-01', 23, '2026-09-01 10:00+00', 200, 'complete', '2026-09-06 00:00+00'),
    ('WIND-2', '2026-09-01', 23, '2026-09-01 10:00+00', 250, 'complete', '2026-09-06 00:00+00'),
    ('WIND-1', '2026-09-01', 25, '2026-09-01 11:00+00', 1000, 'complete', '2026-09-06 00:00+00'),
    ('PS-1', '2026-09-01', 25, '2026-09-01 11:00+00', 0, 'complete', '2026-09-06 00:00+00'),
    ('HYDRO-1', '2026-09-01', 25, '2026-09-01 11:00+00', 0, 'complete', '2026-09-06 00:00+00'),
    ('BIO-1', '2026-09-01', 25, '2026-09-01 11:00+00', 75, 'complete', '2026-09-06 00:00+00'),
    ('WIND-1', '2026-09-01', 27, '2026-09-01 12:00+00', 1000, 'complete', '2026-09-06 00:00+00'),
    ('GAS-1', '2026-09-01', 27, '2026-09-01 12:00+00', 50, 'complete', '2026-09-06 00:00+00'),
    ('WIND-1', '2026-09-01', 29, '2026-09-01 13:00+00', 1000, 'complete', '2026-09-06 00:00+00'),
    ('WIND-2', '2026-09-01', 29, '2026-09-01 13:00+00', 250, 'complete', '2026-09-06 00:00+00'),
    ('GAS-1', '2026-09-01', 29, '2026-09-01 13:00+00', 75, 'complete', '2026-09-06 00:00+00'),
    ('WIND-1', '2026-09-01', 31, '2026-09-01 14:00+00', 999, 'incomplete', '2026-09-06 00:00+00'),
    ('GAS-1', '2026-09-01', 31, '2026-09-01 14:00+00', 50, 'complete', '2026-09-06 00:00+00'),
    ('WIND-1', '2026-09-01', 33, '2026-09-01 15:00+00', 1000, 'complete', '2026-09-06 00:00+00'),
    ('GAS-1', '2026-09-01', 33, '2026-09-01 15:00+00', 50, 'complete', '2026-09-06 00:00+00'),
    ('WIND-1', '2026-09-05', 3, '2026-09-05 00:00+00', 1000, 'complete', '2026-09-06 00:00+00'),
    ('GAS-1', '2026-09-05', 3, '2026-09-05 00:00+00', 50, 'complete', '2026-09-06 00:00+00'),
    ('WIND-1', '2026-09-05', 4, '2026-09-05 00:30+00', 1000, 'complete', '2026-09-06 00:00+00');
create temporary table synthetic_pn (
    national_grid_bm_unit text,
    settlement_date date,
    settlement_period integer,
    time_from timestamptz,
    time_to timestamptz,
    level_from numeric,
    level_to numeric,
    retrieved_at timestamptz
) on commit drop;
insert into synthetic_pn values
    ('WIND-1', '2026-08-31', 47, '2026-08-31 22:00+00', '2026-08-31 22:30+00',
        3000, 3000, '2026-08-30 00:00+00'),
    ('WIND-1', '2026-08-31', 47, '2026-08-31 22:00+00', '2026-08-31 22:30+00',
        2000, 2000, '2026-09-06 00:00+00'),
    ('GAS-1', '2026-08-31', 47, '2026-08-31 22:00+00', '2026-08-31 22:30+00',
        100, 100, '2026-09-06 00:00+00'),
    ('WIND-1', '2026-09-01', 1, '2026-08-31 23:00+00', '2026-08-31 23:30+00',
        2000, 2000, '2026-09-06 00:00+00'),
    ('WIND-2', '2026-09-01', 1, '2026-08-31 23:00+00', '2026-08-31 23:30+00',
        500, 500, '2026-09-06 00:00+00'),
    ('GAS-1', '2026-09-01', 1, '2026-08-31 23:00+00', '2026-08-31 23:30+00',
        100, 100, '2026-09-06 00:00+00'),
    ('BATT-1', '2026-09-01', 1, '2026-08-31 23:00+00', '2026-08-31 23:10+00',
        -50, -50, '2026-09-06 00:00+00'),
    ('BATT-1', '2026-09-01', 1, '2026-08-31 23:10+00', '2026-08-31 23:30+00',
        -50, -50, '2026-09-06 00:00+00'),
    ('WIND-1', '2026-09-01', 23, '2026-09-01 10:00+00', '2026-09-01 10:30+00',
        2000, 2000, '2026-09-06 00:00+00'),
    ('GAS-1', '2026-09-01', 23, '2026-09-01 10:00+00', '2026-09-01 10:30+00',
        400, 400, '2026-09-06 00:00+00'),
    ('WIND-1', '2026-09-01', 25, '2026-09-01 11:00+00', '2026-09-01 11:30+00',
        2000, 2000, '2026-09-06 00:00+00'),
    ('WIND-2', '2026-09-01', 23, '2026-09-01 10:00+00', '2026-09-01 10:30+00',
        500, 500, '2026-09-06 00:00+00'),
    ('PS-1', '2026-09-01', 25, '2026-09-01 11:00+00', '2026-09-01 11:10+00',
        0, 0, '2026-09-06 00:00+00'),
    ('PS-1', '2026-09-01', 25, '2026-09-01 11:10+00', '2026-09-01 11:20+00',
        0, 0, '2026-09-06 00:00+00'),
    ('PS-1', '2026-09-01', 25, '2026-09-01 11:20+00', '2026-09-01 11:30+00',
        60, 60, '2026-09-06 00:00+00'),
    ('HYDRO-1', '2026-09-01', 25, '2026-09-01 11:00+00', '2026-09-01 11:15+00',
        0, 0, '2026-09-06 00:00+00'),
    ('HYDRO-1', '2026-09-01', 25, '2026-09-01 11:15+00', '2026-09-01 11:30+00',
        0, 0, '2026-09-06 00:00+00'),
    ('BIO-1', '2026-09-01', 25, '2026-09-01 11:00+00', '2026-09-01 11:30+00',
        0, 300, '2026-09-06 00:00+00'),
    ('WIND-1', '2026-09-01', 27, '2026-09-01 12:00+00', '2026-09-01 12:30+00',
        2000, 2000, '2026-09-06 00:00+00'),
    ('GAS-1', '2026-09-01', 27, '2026-09-01 12:00+00', '2026-09-01 12:30+00',
        100, 100, '2026-09-06 00:00+00'),
    ('WIND-2', '2026-09-01', 29, '2026-09-01 13:00+00', '2026-09-01 13:30+00',
        500, 500, '2026-09-06 00:00+00'),
    ('GAS-1', '2026-09-01', 29, '2026-09-01 13:00+00', '2026-09-01 13:15+00',
        100, 100, '2026-09-06 00:00+00'),
    ('GAS-1', '2026-09-01', 29, '2026-09-01 13:15+00', '2026-09-01 13:30+00',
        200, 200, '2026-09-06 00:00+00'),
    ('GAS-1', '2026-09-01', 31, '2026-09-01 14:00+00', '2026-09-01 14:30+00',
        100, 100, '2026-09-06 00:00+00'),
    ('WIND-1', '2026-09-01', 31, '2026-09-01 14:00+00', '2026-09-01 14:30+00',
        2000, 2000, '2026-09-06 00:00+00'),
    ('WIND-1', '2026-09-01', 33, '2026-09-01 15:00+00', '2026-09-01 15:30+00',
        2000, 2000, '2026-09-06 00:00+00'),
    ('GAS-1', '2026-09-01', 33, '2026-09-01 15:00+00', '2026-09-01 15:30+00',
        100, 100, '2026-09-06 00:00+00'),
    ('GAS-1', '2026-09-05', 3, '2026-09-05 00:00+00', '2026-09-05 00:30+00',
        100, 100, '2026-09-06 00:00+00'),
    ('WIND-1', '2026-09-05', 4, '2026-09-05 00:30+00', '2026-09-05 01:00+00',
        2000, 2000, '2026-09-06 00:00+00');
"""

Q37_TABLES = {
    "int_elexon__instruction_intervals": "pg_temp.synthetic_intervals",
    "int_elexon__bm_unit_cohort": "pg_temp.synthetic_cohort",
    "stg_elexon__boalf": "pg_temp.synthetic_boalf",
    "fct_commitments": "pg_temp.synthetic_commitments",
    "stg_elexon__pn": "pg_temp.synthetic_pn",
}
Q37_KEY = ("section", "settlement_month", "wind_turn_down_band", "fuel_group", "item")
WIND_DOWN = "wind turned down"
BAND_ENERGY = "energy turned up and down in the band's half-hours"
NO_FUEL_TYPE = "no published fuel type"
BAND_NONE = "0 none"
BAND_UNDER_100_MW = "1 under 100 MW"
BAND_FROM_100_MW = "2 100 MW to under 500 MW"
BAND_FROM_500_MW = "3 500 MW to under 1 GW"
BAND_FROM_1_GW = "4 1 GW to under 2 GW"
BAND_FROM_2_GW = "5 2 GW or more"
BAND_NOT_MEASURED = "6 wind turn-down not measured"


def q37_band_row(band, fuel_group, half_hours, up, flagged_up, down, flagged_down):
    key = (3, None, band, fuel_group, BAND_ENERGY)
    return key, dict(
        half_hours=half_hours,
        turned_up_mwh=up,
        flagged_turned_up_mwh=flagged_up,
        turned_down_mwh=down,
        flagged_turned_down_mwh=flagged_down,
    )


Q37_EXPECTED = dict(
    [
        (
            (1, "2026-08-01", None, "wind", WIND_DOWN),
            dict(
                half_hours=1,
                half_hours_with_flagged_wind_turn_down=1,
                half_hours_at_100_mw_or_more=1,
                half_hours_at_1_gw_or_more=0,
                half_hours_wind_not_measured=0,
                turned_down_mwh="50.000",
                flagged_turned_down_mwh="50.000",
                planned_mwh="1000.000",
                flagged_turned_down_pct_of_planned="5.00",
                first_half_hour="2026-08-31 22:00:00+00:00",
                last_half_hour="2026-08-31 22:00:00+00:00",
            ),
        ),
        (
            (1, "2026-09-01", None, "wind", WIND_DOWN),
            dict(
                half_hours=7,
                half_hours_with_flagged_wind_turn_down=4,
                half_hours_at_100_mw_or_more=3,
                half_hours_at_1_gw_or_more=2,
                half_hours_wind_not_measured=1,
                turned_down_mwh="1910.250",
                flagged_turned_down_mwh="1750.250",
                planned_mwh="7750.000",
                flagged_turned_down_pct_of_planned="22.58",
                first_half_hour="2026-08-31 23:00:00+00:00",
                last_half_hour="2026-09-05 00:00:00+00:00",
            ),
        ),
        (
            (2, None, None, "wind", "wind turned down, all months"),
            dict(
                half_hours=8,
                half_hours_with_flagged_wind_turn_down=5,
                half_hours_at_100_mw_or_more=4,
                half_hours_at_1_gw_or_more=2,
                half_hours_wind_not_measured=1,
                turned_down_mwh="1960.250",
                flagged_turned_down_mwh="1800.250",
                planned_mwh="8750.000",
                flagged_turned_down_pct_of_planned="20.57",
                first_half_hour="2026-08-31 22:00:00+00:00",
                last_half_hour="2026-09-05 00:00:00+00:00",
            ),
        ),
        q37_band_row(BAND_NONE, "wind", 2, "0.000", "0.000", "100.000", "0.000"),
        q37_band_row(
            BAND_NONE, "gas_turbine", 2, "45.000", "20.000", "10.000", "0.000"
        ),
        q37_band_row(BAND_UNDER_100_MW, "wind", 1, "10.000", "0.000", "0.250", "0.250"),
        q37_band_row(
            BAND_UNDER_100_MW, "gas_turbine", 1, "56.250", "0.000", "6.250", "0.000"
        ),
        q37_band_row(BAND_FROM_100_MW, "wind", 1, "0.000", "0.000", "50.000", "50.000"),
        q37_band_row(
            BAND_FROM_100_MW, "gas_turbine", 1, "100.000", "0.000", "0.000", "0.000"
        ),
        q37_band_row(
            BAND_FROM_500_MW, "wind", 1, "0.000", "0.000", "300.000", "250.000"
        ),
        q37_band_row(
            BAND_FROM_500_MW, "gas_turbine", 1, "200.000", "200.000", "0.000", "0.000"
        ),
        q37_band_row(
            BAND_FROM_500_MW, NO_FUEL_TYPE, 1, "10.000", "0.000", "0.000", "0.000"
        ),
        q37_band_row(BAND_FROM_1_GW, "wind", 1, "0.000", "0.000", "500.000", "500.000"),
        q37_band_row(
            BAND_FROM_1_GW, "gas_turbine", 1, "0.000", "0.000", "50.000", "0.000"
        ),
        q37_band_row(
            BAND_FROM_2_GW, "wind", 1, "0.000", "0.000", "1000.000", "1000.000"
        ),
        q37_band_row(
            BAND_FROM_2_GW, "pumped_storage", 1, "20.000", "20.000", "0.000", "0.000"
        ),
        q37_band_row(BAND_FROM_2_GW, "hydro", 1, "43.750", "0.000", "0.000", "0.000"),
        q37_band_row(
            BAND_FROM_2_GW, "bioenergy", 1, "0.000", "0.000", "43.750", "0.000"
        ),
        (
            (4, None, None, "wind", "left out: no complete PN capture, flagged"),
            dict(stretches=1, level_energy_mwh="700.000"),
        ),
        (
            (
                4,
                None,
                None,
                "wind",
                "left out: no single ramp segment spans the stretch, flagged",
            ),
            dict(stretches=1, level_energy_mwh="900.000"),
        ),
        q37_band_row(
            BAND_NOT_MEASURED, "gas_turbine", 1, "15.000", "0.000", "0.000", "0.000"
        ),
        q37_band_row(BAND_NOT_MEASURED, "wind", 1, "0.000", "0.000", "10.000", "0.000"),
        (
            (
                4,
                None,
                None,
                "pumped_storage",
                "left out: no complete PN capture, not flagged",
            ),
            dict(stretches=1, level_energy_mwh="5.000"),
        ),
        (
            (
                4,
                None,
                None,
                "gas_turbine",
                "stretches whose end levels do not reproduce the stored instructed_mwh",
            ),
            dict(stretches=1, level_energy_mwh="81.000"),
        ),
        (
            (
                4,
                None,
                None,
                "gas_turbine",
                "measured stretches with no flag value, counted as not flagged",
            ),
            dict(
                stretches=1,
                turned_up_mwh="10.000",
                flagged_turned_up_mwh="0",
                turned_down_mwh="0.000",
                flagged_turned_down_mwh="0",
            ),
        ),
        (
            (
                4,
                None,
                None,
                "gas_turbine",
                "energy in half-hours with no complete wind PN capture",
            ),
            dict(
                half_hours=1,
                turned_up_mwh="50.000",
                flagged_turned_up_mwh="0.000",
                turned_down_mwh="0.000",
                flagged_turned_down_mwh="0.000",
            ),
        ),
    ]
)


# Q38: HEYM11 is registered at 600 MW (300 MWh a half-hour) and HEYM12 at
# 500 MW (250 MWh). Times are UTC; London is an hour ahead, so 23:00 on
# 31 August is settlement date 1 September.
#
# 22:30 on 31 August 2025 (settlement date 31 August, before the first
#   date): HEYM11 meters 300 and HUNB-7 50; neither is read.
# 23:00 on 31 August 2025 (September 2025): HEYM11 meters 300, its capacity:
#   no gap. HEYM12 meters exactly 0: not generating, gap 250. No notice.
# 22:00 on 31 August 2026: HEYM12 has no metered value: left out, though a
#   notice names it then.
# 22:30 on 31 August 2026 (August 2026): HEYM11 meters 150 with a planned
#   notice: gap 150. HEYM12 meters -10 with an unplanned notice: output 0,
#   10 drawn, gap 250, not generating. TORN-1 (capacity 0) and TORN-2 (no
#   capacity) meter 5 and 7: left out. HUNB-7, a NUCLEAR registry unit
#   outside the listed ten, meters -2; GASX-1, a gas unit with a notice,
#   meters 100 and is nowhere.
# 23:00 on 31 August 2026 (September 2026): HEYM11 meters 310 with planned
#   and unplanned notices both in force: 10 above capacity, no gap. HEYM12
#   meters 0.001: generating, gap 249.999, no notice. HUNB-7 meters 3.
# 00:00 on 5 September 2026, the cutoff: HEYM11 meters 200 from run II on
#   a row whose mapping status is conflict: gap 100, no notice. 00:30:
#   HEYM11 meters 0, after the cutoff.
# HEYM11 also has a row with no period start (29 March 2026): left out.
# DNGB21, the other NUCLEAR registry unit outside the ten, has no metered
# row: 0 half-hours and 0 MWh.
#
# September 2025: 300 of 550, 54.55%, gap 250. August 2026: 150 of 550,
# 27.27%, gap 400. September 2026: 510.001 of 850, 60.00%, gap 349.999.
# All months: 960.001 of 1,950, 49.23%; gap 999.999, 10 above capacity, and
# 960.001 + 999.999 - 10 = 1,950.
#
# The registry lists four of the ten units once (HEYM11, HEYM12, TORN-1,
# TORN-2) and SIZB-2 twice, so six are without exactly one row. No
# half-hour holds all ten, so all four half-hours read are counted as
# short of them.
#
# HEYM11's notices: normal capacity 580, 610, 590 and 600 count (the third
# ends a second after the first settlement date begins, the fourth starts a
# second before the cutoff's half-hour ends). Six do not: Dismissed, a
# consumption unavailability, other market information, an ambiguous
# mapping, one ending as the first settlement date begins and one starting
# as the cutoff's half-hour ends.
Q38 = "q38_nuclear_output_and_outage_notices"
Q38_FIXTURES = """
create temporary table synthetic_registry (
    national_grid_bm_unit text,
    elexon_bm_unit text,
    fuel_type text,
    generation_capacity_mw numeric
) on commit drop;
insert into synthetic_registry values
    ('HEYM11', 'T_HEYM11', 'NUCLEAR', 600),
    ('HEYM12', 'T_HEYM12', 'NUCLEAR', 500),
    ('TORN-1', 'T_TORN-1', 'NUCLEAR', 0),
    ('TORN-2', 'T_TORN-2', 'NUCLEAR', null),
    ('SIZB-2', 'T_SIZB-2', 'NUCLEAR', 630),
    ('SIZB-2', 'T_SIZB-2B', 'NUCLEAR', 630),
    ('HUNB-7', 'T_HUNB-7', 'NUCLEAR', 510),
    ('DNGB21', 'T_DNGB21', 'NUCLEAR', 0),
    ('GASX-1', 'T_GASX-1', 'CCGT', 400);

create temporary table synthetic_generation (
    bm_unit text,
    settlement_date date,
    period_start_utc timestamptz,
    latest_quantity_mwh numeric,
    latest_run_code text,
    mapping_status text
) on commit drop;
insert into synthetic_generation values
    ('T_HEYM11', '2025-08-31', '2025-08-31 22:30+00', 300, 'SF', 'mapped'),
    ('T_HUNB-7', '2025-08-31', '2025-08-31 22:30+00', 50, 'SF', 'mapped'),
    ('T_HEYM11', '2025-09-01', '2025-08-31 23:00+00', 300, 'SF', 'mapped'),
    ('T_HEYM12', '2025-09-01', '2025-08-31 23:00+00', 0, 'SF', 'mapped'),
    ('T_HEYM11', '2026-03-29', null, 5, 'SF', 'mapped'),
    ('T_HEYM12', '2026-08-31', '2026-08-31 22:00+00', null, 'SF', 'mapped'),
    ('T_HEYM11', '2026-08-31', '2026-08-31 22:30+00', 150, 'SF', 'mapped'),
    ('T_HEYM12', '2026-08-31', '2026-08-31 22:30+00', -10, 'SF', 'mapped'),
    ('T_TORN-1', '2026-08-31', '2026-08-31 22:30+00', 5, 'SF', 'mapped'),
    ('T_TORN-2', '2026-08-31', '2026-08-31 22:30+00', 7, 'SF', 'mapped'),
    ('T_HUNB-7', '2026-08-31', '2026-08-31 22:30+00', -2, 'SF', 'mapped'),
    ('T_GASX-1', '2026-08-31', '2026-08-31 22:30+00', 100, 'SF', 'mapped'),
    ('T_HEYM11', '2026-09-01', '2026-08-31 23:00+00', 310, 'SF', 'mapped'),
    ('T_HEYM12', '2026-09-01', '2026-08-31 23:00+00', 0.001, 'SF', 'mapped'),
    ('T_HUNB-7', '2026-09-01', '2026-08-31 23:00+00', 3, 'SF', 'mapped'),
    ('T_HEYM11', '2026-09-05', '2026-09-05 00:00+00', 200, 'II', 'conflict'),
    ('T_HEYM11', '2026-09-05', '2026-09-05 00:30+00', 0, 'II', 'mapped');

create temporary table synthetic_known_outages (
    national_grid_bm_unit text,
    period_start_utc timestamptz,
    known_class text
) on commit drop;
insert into synthetic_known_outages values
    ('HEYM12', '2026-08-31 22:00+00', 'planned'),
    ('HEYM11', '2026-08-31 22:30+00', 'planned'),
    ('HEYM12', '2026-08-31 22:30+00', 'unplanned'),
    ('GASX-1', '2026-08-31 22:30+00', 'planned'),
    ('HEYM11', '2026-08-31 23:00+00', 'both');

create temporary table synthetic_publications (
    national_grid_bm_unit text,
    mapping_rule text,
    message_type text,
    event_type text,
    event_status text,
    normal_capacity numeric,
    event_start_time timestamptz,
    event_end_time timestamptz
) on commit drop;
insert into synthetic_publications values
    ('HEYM11', 'asset_id', 'UnavailabilitiesOfElectricityFacilities',
        'Production unavailability', 'Active', 580,
        '2026-01-01 00:00+00', '2026-02-01 00:00+00'),
    ('HEYM11', 'affected_unit', 'UnavailabilitiesOfElectricityFacilities',
        'Production unavailability', 'Active', 610,
        '2025-07-01 00:00+00', null),
    ('HEYM11', 'asset_id', 'UnavailabilitiesOfElectricityFacilities',
        'Production unavailability', 'Active', 590,
        '2025-08-01 00:00+00', '2025-08-31 23:00:01+00'),
    ('HEYM11', 'asset_id', 'UnavailabilitiesOfElectricityFacilities',
        'Production unavailability', 'Active', 600,
        '2026-09-05 00:29:59+00', '2026-10-01 00:00+00'),
    ('HEYM11', 'asset_id', 'UnavailabilitiesOfElectricityFacilities',
        'Production unavailability', 'Dismissed', 999,
        '2026-01-01 00:00+00', '2026-02-01 00:00+00'),
    ('HEYM11', 'asset_id', 'UnavailabilitiesOfElectricityFacilities',
        'Consumption unavailability', 'Active', 998,
        '2026-01-01 00:00+00', '2026-02-01 00:00+00'),
    ('HEYM11', 'asset_id', 'OtherMarketInformation',
        'Production unavailability', 'Active', 997,
        '2026-01-01 00:00+00', '2026-02-01 00:00+00'),
    ('HEYM11', 'ambiguous', 'UnavailabilitiesOfElectricityFacilities',
        'Production unavailability', 'Active', 996,
        '2026-01-01 00:00+00', '2026-02-01 00:00+00'),
    ('HEYM11', 'asset_id', 'UnavailabilitiesOfElectricityFacilities',
        'Production unavailability', 'Active', 995,
        '2025-08-01 00:00+00', '2025-08-31 23:00+00'),
    ('HEYM11', 'asset_id', 'UnavailabilitiesOfElectricityFacilities',
        'Production unavailability', 'Active', 994,
        '2026-09-05 00:30+00', '2026-10-01 00:00+00');
"""

Q38_TABLES = {
    "int_elexon__bm_units_current": "pg_temp.synthetic_registry",
    "fct_generation": "pg_temp.synthetic_generation",
    "int_remit__known_outage_periods": "pg_temp.synthetic_known_outages",
    "int_remit__revisions": "pg_temp.synthetic_publications",
}
Q38_KEY = (
    "section",
    "settlement_month",
    "national_grid_bm_unit",
    "notice_class",
    "output_state",
    "item",
)
OUTPUT_AGAINST_CAPACITY = "output against registered capacity"
GAP_BELOW_CAPACITY = "gap below registered capacity"
GENERATING = "generating"
NOT_GENERATING = "not generating"
FIRST_HALF_HOUR_READ = "2025-08-31 23:00:00+00:00"
AUGUST_HALF_HOUR = "2026-08-31 22:30:00+00:00"
SEPTEMBER_HALF_HOUR = "2026-08-31 23:00:00+00:00"
CUTOFF_HALF_HOUR = "2026-09-05 00:00:00+00:00"


def q38_output_row(section, month, cells):
    return (section, month, None, None, None, OUTPUT_AGAINST_CAPACITY), cells


def q38_gap_row(section, month, notice_class, output_state, cells):
    key = (section, month, None, notice_class, output_state, GAP_BELOW_CAPACITY)
    return key, cells


def q38_gap_cells(units, unit_half_hours, gap, share):
    return dict(
        units=units,
        unit_half_hours=unit_half_hours,
        gap_mwh=gap,
        share_of_gap_pct=share,
    )


def q38_coverage_row(item, cells, unit=None):
    return (6, None, unit, None, None, item), cells


Q38_EXPECTED = dict(
    [
        q38_output_row(
            1,
            "2025-09-01",
            dict(
                units=2,
                unit_half_hours=2,
                capacity_mwh="550.0",
                output_mwh="300.0",
                output_pct_of_capacity="54.55",
                gap_mwh="250.0",
                above_capacity_mwh="0.0",
                drawn_mwh="0.0",
                first_half_hour=FIRST_HALF_HOUR_READ,
                last_half_hour=FIRST_HALF_HOUR_READ,
            ),
        ),
        q38_output_row(
            1,
            "2026-08-01",
            dict(
                units=2,
                unit_half_hours=2,
                capacity_mwh="550.0",
                output_mwh="150.0",
                output_pct_of_capacity="27.27",
                gap_mwh="400.0",
                above_capacity_mwh="0.0",
                drawn_mwh="10.0",
                first_half_hour=AUGUST_HALF_HOUR,
                last_half_hour=AUGUST_HALF_HOUR,
            ),
        ),
        q38_output_row(
            1,
            "2026-09-01",
            dict(
                units=2,
                unit_half_hours=3,
                capacity_mwh="850.0",
                output_mwh="510.0",
                output_pct_of_capacity="60.00",
                gap_mwh="350.0",
                above_capacity_mwh="10.0",
                drawn_mwh="0.0",
                first_half_hour=SEPTEMBER_HALF_HOUR,
                last_half_hour=CUTOFF_HALF_HOUR,
            ),
        ),
        q38_output_row(
            2,
            None,
            dict(
                units=2,
                unit_half_hours=7,
                capacity_mwh="1950.0",
                output_mwh="960.0",
                output_pct_of_capacity="49.23",
                gap_mwh="1000.0",
                above_capacity_mwh="10.0",
                drawn_mwh="10.0",
                first_half_hour=FIRST_HALF_HOUR_READ,
                last_half_hour=CUTOFF_HALF_HOUR,
            ),
        ),
        q38_gap_row(
            3, "2025-09-01", "none", GENERATING, q38_gap_cells(1, 1, "0.0", "0.00")
        ),
        q38_gap_row(
            3,
            "2025-09-01",
            "none",
            NOT_GENERATING,
            q38_gap_cells(1, 1, "250.0", "100.00"),
        ),
        q38_gap_row(
            3,
            "2026-08-01",
            "planned",
            GENERATING,
            q38_gap_cells(1, 1, "150.0", "37.50"),
        ),
        q38_gap_row(
            3,
            "2026-08-01",
            "unplanned",
            NOT_GENERATING,
            q38_gap_cells(1, 1, "250.0", "62.50"),
        ),
        q38_gap_row(
            3, "2026-09-01", "both", GENERATING, q38_gap_cells(1, 1, "0.0", "0.00")
        ),
        q38_gap_row(
            3,
            "2026-09-01",
            "none",
            GENERATING,
            q38_gap_cells(2, 2, "350.0", "100.00"),
        ),
        q38_gap_row(4, None, "both", GENERATING, q38_gap_cells(1, 1, "0.0", "0.00")),
        q38_gap_row(4, None, "none", GENERATING, q38_gap_cells(2, 3, "350.0", "35.00")),
        q38_gap_row(
            4, None, "none", NOT_GENERATING, q38_gap_cells(1, 1, "250.0", "25.00")
        ),
        q38_gap_row(
            4, None, "planned", GENERATING, q38_gap_cells(1, 1, "150.0", "15.00")
        ),
        q38_gap_row(
            4,
            None,
            "unplanned",
            NOT_GENERATING,
            q38_gap_cells(1, 1, "250.0", "25.00"),
        ),
        (
            (5, None, "HEYM11", None, None, "unit, all months"),
            dict(
                station="Heysham 1",
                unit_half_hours=4,
                capacity_mwh="1200.0",
                output_mwh="960.0",
                output_pct_of_capacity="80.00",
                gap_mwh="250.0",
                above_capacity_mwh="10.0",
                drawn_mwh="0.0",
                registry_capacity_mw="600",
                highest_half_hour_mw="620",
                notice_normal_capacity_low_mw="580",
                notice_normal_capacity_high_mw="610",
                notice_publications=4,
                first_half_hour=FIRST_HALF_HOUR_READ,
                last_half_hour=CUTOFF_HALF_HOUR,
            ),
        ),
        (
            (5, None, "HEYM12", None, None, "unit, all months"),
            dict(
                station="Heysham 1",
                unit_half_hours=3,
                capacity_mwh="750.0",
                output_mwh="0.0",
                output_pct_of_capacity="0.00",
                gap_mwh="750.0",
                above_capacity_mwh="0.0",
                drawn_mwh="10.0",
                registry_capacity_mw="500",
                highest_half_hour_mw="0.002",
                notice_normal_capacity_low_mw=None,
                notice_normal_capacity_high_mw=None,
                notice_publications=None,
                first_half_hour=FIRST_HALF_HOUR_READ,
                last_half_hour=SEPTEMBER_HALF_HOUR,
            ),
        ),
        q38_coverage_row(
            "unit half-hours by metered run: SF",
            dict(units=2, unit_half_hours=6, output_mwh="760.0", drawn_mwh="10.0"),
        ),
        q38_coverage_row(
            "unit half-hours by metered run: II",
            dict(units=1, unit_half_hours=1, output_mwh="200.0", drawn_mwh="0.0"),
        ),
        q38_coverage_row(
            "unit half-hours by mapping status: mapped",
            dict(units=2, unit_half_hours=6, output_mwh="760.0", drawn_mwh="10.0"),
        ),
        q38_coverage_row(
            "unit half-hours by mapping status: conflict",
            dict(units=1, unit_half_hours=1, output_mwh="200.0", drawn_mwh="0.0"),
        ),
        q38_coverage_row("left out: no period start", dict(units=1, unit_half_hours=1)),
        q38_coverage_row(
            "left out: no metered value", dict(units=1, unit_half_hours=1)
        ),
        q38_coverage_row(
            "left out: no positive registry capacity",
            dict(units=2, unit_half_hours=2),
        ),
        q38_coverage_row(
            "half-hours without all 10 listed units", dict(unit_half_hours=4)
        ),
        q38_coverage_row(
            "listed units without exactly one NUCLEAR registry row", dict(units=6)
        ),
        q38_coverage_row(
            "registry NUCLEAR unit outside the listed units",
            dict(
                unit_half_hours=2,
                output_mwh="3.0",
                drawn_mwh="2.0",
                registry_capacity_mw="510",
            ),
            unit="HUNB-7",
        ),
        q38_coverage_row(
            "registry NUCLEAR unit outside the listed units",
            dict(
                unit_half_hours=0,
                output_mwh="0.0",
                drawn_mwh="0.0",
                registry_capacity_mw="0",
            ),
            unit="DNGB21",
        ),
    ]
)


# Q8 and Q28 read the fixed window, settlement dates 22 August 2025 to 16
# August 2026 (360 dates, 17,280 half-hours: the 50-period and 46-period days
# cancel out), all in summer time here. A (22 August 2025, the first date)
# has all three values in every half-hour. B (23 August) has them in 46:
# period 47 has a null demand error and period 48 a null final forecast. C
# (16 August 2026, the last date) has them in 45: no shortfall in period 46
# (one null value), no demand row in 47, no carbon row in 48. So A and B are
# matched and C is not. The dates either side of the window have all three
# in every half-hour. C and those dates carry larger values than any on A
# or B, so letting one in would move every rank. U2's -5 in every half-hour
# adds nothing once its positive part is taken; U3 (outside the cohort) and
# U4 (not determinable) put 100 in A's first half-hour and must not count.
#
# On A and B, half-hour p has abs(error_api_final) p (sign alternating), so
# A's and B's p tie: 46 tied pairs, then A47 and A48. The demand error and
# the shortfall follow the same tie pattern. Each half-hour p takes a place:
# p itself, except inside a reversed block of periods (a to b), where it
# takes a + b - p. The abs demand error is 10 x place squared MW (sign
# alternating) and the shortfall is place MWh, so raw values are not a
# straight line in their ranks. The shortfall reverses periods 11 to 38;
# each case below chooses the demand blocks.
#
# Spearman with average ranks: pair p ranks 2p - 0.5, A47 93 and A48 94, mean
# 47.5, and every measure's squared deviations sum to 94 x (94^2 - 1) / 12 -
# 46 x 0.5 = 69,184.5. Moving p to place q changes two ranks by 2(p - q), so
# with D the sum of (p - q)^2, rho = 1 - 8D / 138,369. Reversing a block of n
# periods gives D = n(n^2 - 1) / 3: the shortfall's 28 give 7,308 and
# rho_shortfall = 79,905 / 138,369 = 0.577478.
#
# Bootstrap with two matched days: a draw is A twice (272 draws with this
# seed), A and B (506, the observed sample) or B twice (222). A twice ties
# every value with its copy, ranks 2r - 0.5 of A's own, so its correlations
# are A's alone: no ties, rho = 1 - 6D / (48 x 2,303) = 1 - D / 18,424. B
# twice likewise gives 1 - D / 16,215. Both are over 5% of the draws, so the
# 5th and 95th percentiles fall on their values.
#
# The main case reverses demand periods 1 to 16: D 1,360, rho_demand
# 127,489 / 138,369 = 0.921370; gap 8 x (7,308 - 1,360) / 138,369 =
# 0.343892, demand stronger. A twice: 5,948 / 18,424 = 0.322840; B twice:
# 5,948 / 16,215 = 0.366821.
#
# Q28 on the same rows: ntile(5) of 94 is 19, 19, 19, 19, 18, in the order A1,
# B1, A2, B2, ... A46, B46, A47, A48. Quintile 1 is A1 to A10 and B1 to B9:
# 100 / 19 = 5.263158. Quintile 2 is B10 and both days' 11 to 19 (places 38
# to 30): (10 + 2 x 306) / 19. Quintile 3 is both days' 20 to 28 (29 to 21)
# and A29 (20): 470 / 19. Quintile 4 is B29 (20) and both days' 30 to 38 (19
# to 11): 290 / 19. Quintile 5 is both days' 39 to 46 and A47, A48:
# (2 x 340 + 95) / 18 = 43.055556. Ratio 775 x 19 / (18 x 100) = 8.180556:
# supported. A twice (20, 19, 19, 19, 19): bottom A1 to A10 twice, 5.5; top
# the second A39 and A40 to A48 twice, 831 / 19; ratio 7.952153. B twice (19,
# 19, 18, 18, 18): bottom B1 to B9 twice and one B10, 100 / 19; top B38
# twice (place 11) and B39 to B46 twice, 702 / 18 = 39; ratio 7.41. The
# observed sample is half the draws, so the interval is 7.41 to 8.180556.
Q8_Q28 = "q8_q28_history"
# The bootstrap's fixed seed, as in the Q8 and Q28 analysis.
HISTORY_BOOTSTRAP_SEED = 20261025
MAIN_DEMAND_BLOCKS = "(1, 16)"
Q8_Q28_FIXTURES = """
create temporary table synthetic_carbon_outturn (
    period_start timestamptz,
    actual integer,
    api_final_forecast integer
) on commit drop;
create temporary table synthetic_demand_error (
    settlement_date date,
    start_time timestamptz,
    demand_error_final_mw integer,
    forecast_published_at timestamptz,
    outturn_published_at timestamptz
) on commit drop;
create temporary table synthetic_shortfall (
    national_grid_bm_unit text,
    settlement_date date,
    period_start_utc timestamptz,
    unexplained_shortfall_mwh numeric,
    determinability text
) on commit drop;
create temporary table synthetic_cohort (national_grid_bm_unit text) on commit drop;
insert into synthetic_cohort values ('U1'), ('U2'), ('U4');

create temporary table synthetic_places on commit drop as
select
    periods.period,
    coalesce(demand_blocks.first_period + demand_blocks.last_period - periods.period,
        periods.period) as demand_place,
    coalesce(shortfall_blocks.first_period + shortfall_blocks.last_period - periods.period,
        periods.period) as shortfall_place
from generate_series(1, 48) as periods (period)
left join (values {demand_blocks}) as demand_blocks (first_period, last_period)
    on periods.period between demand_blocks.first_period and demand_blocks.last_period
left join (values (11, 38)) as shortfall_blocks (first_period, last_period)
    on periods.period between shortfall_blocks.first_period and shortfall_blocks.last_period;

create temporary table synthetic_periods on commit drop as
select
    days.day,
    days.settlement_date,
    places.period,
    (days.settlement_date::timestamp at time zone 'Europe/London')
        + (places.period - 1) * interval '30 minutes' as period_start,
    case when places.period % 2 = 1 then 1 else -1 end as error_sign,
    case
        when days.day not in ('A', 'B') then 1000 + places.period
        else places.period
    end as abs_carbon_error,
    case
        when days.day not in ('A', 'B') then 100000 + places.period
        when days.day = 'B' and {constant_b} then 0
        else 10 * places.demand_place * places.demand_place
    end as abs_demand_error,
    case
        when days.day not in ('A', 'B') then 5000 + places.period
        when {constant_shortfall} is not null then {constant_shortfall}
        when days.day = 'B' and {constant_b} and places.period <= 10 then 0
        when places.period <= 10 and {bottom_shortfall} is not null then {bottom_shortfall}
        when places.period >= 39 and {top_shortfall} is not null then {top_shortfall}
        else places.shortfall_place
    end as shortfall
from (
    values ('A', date '2025-08-22'), ('B', date '2025-08-23'), ('C', date '2026-08-16'),
        ('before', date '2025-08-21'), ('after', date '2026-08-17')
) as days (day, settlement_date)
cross join synthetic_places as places;

insert into synthetic_carbon_outturn
select
    period_start,
    2000 + error_sign * abs_carbon_error,
    case when day = 'B' and period = 48 then null else 2000 end
from synthetic_periods
where not (day = 'C' and period = 48);

insert into synthetic_demand_error
select
    settlement_date,
    period_start,
    case when day = 'B' and period = 47 then null else -error_sign * abs_demand_error end,
    period_start - case when day = 'A' and period = 5 then interval '3 hours' else interval '14 minutes' end,
    period_start + case when day = 'B' and period = 6 then interval '150 minutes' else interval '30 minutes' end
from synthetic_periods
where {with_demand}
    and not (day = 'C' and period = 47);

insert into synthetic_shortfall
select
    'U1',
    settlement_date,
    period_start,
    case when day = 'C' and period = 46 then null else shortfall end,
    'determinable'
from synthetic_periods
union all
select 'U2', settlement_date, period_start, -5, 'determinable'
from synthetic_periods
where not (day = 'C' and period = 46)
union all
select 'U3', settlement_date, period_start, 100, 'determinable'
from synthetic_periods
where day = 'A' and period = 1
union all
select 'U4', settlement_date, period_start, 100, 'not determinable'
from synthetic_periods
where day = 'A' and period = 1;
"""

Q8_Q28_TABLES = {
    "int_carbon_outturn__latest": "pg_temp.synthetic_carbon_outturn",
    "int_demand_error_by_period": "pg_temp.synthetic_demand_error",
    "int_shortfall_by_unit_period": "pg_temp.synthetic_shortfall",
    "int_elexon__bm_unit_cohort": "pg_temp.synthetic_cohort",
}

HISTORY_POPULATION = (
    1,
    "population: matched days (all three values in at least 46 half-hours), "
    "every half-hour on them with all three",
    None,
)
RHO_DEMAND = (
    2,
    "rho_demand: abs(error_api_final) with abs(demand_error_final_mw)",
    None,
)
RHO_SHORTFALL = (2, "rho_shortfall: abs(error_api_final) with positive shortfall", None)
GAP = (2, "gap, rho_demand minus rho_shortfall", None)
Q8_VERDICT = (2, "verdict (stronger at a gap of 0.1 or more either way)", None)
GAP_DRAWS = (2, "bootstrap draws with a gap (900 of 1000 needed)", None)
GAP_LOW = (2, "bootstrap 90% interval for the gap, low", None)
GAP_HIGH = (2, "bootstrap 90% interval for the gap, high", None)
Q28_BOTTOM = (3, "bottom quintile mean, MWh", None)
Q28_TOP = (3, "top quintile mean, MWh", None)
Q28_DIFFERENCE = (3, "difference, top minus bottom, MWh", None)
Q28_RATIO = (3, "ratio, top over bottom", None)
Q28_VERDICT = (3, "verdict (supported at a ratio of 1.25 or more)", None)
RATIO_DRAWS = (3, "bootstrap draws with a ratio (900 of 1000 needed)", None)
RATIO_LOW = (3, "bootstrap 90% interval for the ratio, low", None)
RATIO_HIGH = (3, "bootstrap 90% interval for the ratio, high", None)
HISTORY_QUINTILE = "quintile of abs(error_api_final): mean positive shortfall, MWh"
Q28_BOTTOM_TIES = (
    "half-hours sharing the bottom quintile's largest abs(error_api_final) "
    "(value: that error)"
)
DEMAND_HALF_HOURS = (5, "half-hours with demand_error_final_mw", None)
NOT_MATCHED = (5, "settlement dates not matched", None)
STALE_FORECASTS = (
    "population half-hours whose last forecast was issued over 30 minutes "
    "before them (value: the oldest, minutes)"
)
LATE_OUTTURNS = (
    "population half-hours whose first outturn was published over 60 minutes "
    "after they ended (value: the latest, minutes)"
)

Q8_Q28_EXPECTED = {
    HISTORY_POPULATION: dict(
        half_hours=94, days=2, first_date="2025-08-22", last_date="2025-08-23"
    ),
    RHO_DEMAND: dict(half_hours=94, value="0.921370"),
    RHO_SHORTFALL: dict(half_hours=94, value="0.577478"),
    GAP: dict(value="0.343892"),
    Q8_VERDICT: dict(result="demand stronger"),
    GAP_DRAWS: dict(value="1000", result="available"),
    GAP_LOW: dict(value="0.322840"),
    GAP_HIGH: dict(value="0.366821"),
    Q28_BOTTOM: dict(half_hours=19, value="5.263158"),
    Q28_TOP: dict(half_hours=18, value="43.055556"),
    Q28_DIFFERENCE: dict(value="37.792398"),
    Q28_RATIO: dict(value="8.180556"),
    Q28_VERDICT: dict(result="supported"),
    RATIO_DRAWS: dict(value="1000", result="available"),
    RATIO_LOW: dict(value="7.410000"),
    RATIO_HIGH: dict(value="8.180556"),
    (4, HISTORY_QUINTILE, 1): dict(
        half_hours=19, value="5.263158", result="abs(error_api_final) 1 to 10"
    ),
    (4, HISTORY_QUINTILE, 2): dict(
        half_hours=19, value="32.736842", result="abs(error_api_final) 10 to 19"
    ),
    (4, HISTORY_QUINTILE, 3): dict(
        half_hours=19, value="24.736842", result="abs(error_api_final) 20 to 29"
    ),
    (4, HISTORY_QUINTILE, 4): dict(
        half_hours=19, value="15.263158", result="abs(error_api_final) 29 to 38"
    ),
    (4, HISTORY_QUINTILE, 5): dict(
        half_hours=18, value="43.055556", result="abs(error_api_final) 39 to 48"
    ),
    (5, "settlement dates in the window", None): dict(
        half_hours=17280, days=360, first_date="2025-08-22", last_date="2026-08-16"
    ),
    (5, "half-hours with error_api_final", None): dict(half_hours=142),
    DEMAND_HALF_HOURS: dict(half_hours=142),
    (5, "half-hours with the cohort shortfall", None): dict(half_hours=143),
    (5, "half-hours with all three values", None): dict(half_hours=139, days=3),
    (5, "settlement dates without error_api_final in any half-hour", None): dict(
        days=357
    ),
    (5, "settlement dates without demand_error_final_mw in any half-hour", None): (
        dict(days=357)
    ),
    (5, "settlement dates without the cohort shortfall in any half-hour", None): (
        dict(days=357)
    ),
    NOT_MATCHED: dict(days=358),
    (5, "half-hours on matched days without all three values", None): dict(
        half_hours=2, days=1
    ),
    # A10 and B10 share the bottom quintile's largest error, 10; one is in it.
    (4, Q28_BOTTOM_TIES, None): dict(
        half_hours=2, value="10", result="in the bottom quintile: 1"
    ),
    # A5's forecast was issued 3 hours before it, the rest 14 minutes; B6's
    # outturn came 2 hours after it ended, the rest as it ended.
    (5, STALE_FORECASTS, None): dict(half_hours=1, value="180.000000"),
    (5, LATE_OUTTURNS, None): dict(half_hours=1, value="120.000000"),
}

# Every population half-hour's shortfall at 7: rho_shortfall has no value, so
# there is no gap and no verdict; Q28's means are equal, a ratio of 1.
Q8_Q28_CONSTANT_SHORTFALL_EXPECTED = {
    RHO_DEMAND: dict(half_hours=94, value="0.921370"),
    RHO_SHORTFALL: dict(half_hours=94, value=None),
    GAP: dict(value=None),
    Q8_VERDICT: dict(result="not applicable: a measure is constant"),
    GAP_DRAWS: dict(value=None, result="not applicable"),
    GAP_LOW: dict(value=None),
    GAP_HIGH: dict(value=None),
    Q28_BOTTOM: dict(value="7.000000"),
    Q28_TOP: dict(value="7.000000"),
    Q28_RATIO: dict(value="1.000000"),
    Q28_VERDICT: dict(result="not supported"),
}

# Other demand blocks against the same shortfall (D 7,308). Periods 1 to 46
# reversed: D 32,430, rho_demand -0.874986, gap -1.452464. Periods 1 to 28
# reversed: D 7,308, the shortfall's own, so the gap is 0. Near 0.1: blocks
# of 25, 10, 5 and 3 periods give D 5,200 + 330 + 40 + 8 = 5,578, gap
# 8 x 1,730 / 138,369 = 0.100022; one more swapped pair gives 5,580, gap
# 8 x 1,728 / 138,369 = 0.099907. Near -0.1: 30, 5 and 3 periods give
# 8,990 + 40 + 8 = 9,038, gap -0.100022; 30, 5 and three swapped pairs give
# 9,036, gap -0.099907. Minimum ranks in place of average ranks would put
# the 0.100022 gap at 0.099889.
Q8_GAP_CASES = (
    ("(1, 46)", "-0.874986", "-1.452464", "shortfall stronger"),
    ("(1, 28)", "0.577478", "0.000000", "no clear difference"),
    (
        "(1, 25), (26, 35), (36, 40), (41, 43)",
        "0.677500",
        "0.100022",
        "demand stronger",
    ),
    (
        "(1, 25), (26, 35), (36, 40), (41, 43), (44, 45)",
        "0.677384",
        "0.099907",
        "no clear difference",
    ),
    ("(1, 30), (31, 35), (36, 38)", "0.477455", "-0.100022", "shortfall stronger"),
    (
        "(1, 30), (31, 35), (36, 37), (38, 39), (40, 41)",
        "0.477571",
        "-0.099907",
        "no clear difference",
    ),
)

# B's demand error 0 in every half-hour and its shortfall 0 in periods 1 to
# 10: a draw of B twice has a constant demand measure (no gap) and a bottom
# quintile of zeros (no ratio), so only the 778 other draws are usable, under
# 900. Q28: bottom A1 to A10 (55) and B1 to B9 (0) over 19 = 2.894737; ratio
# 775 x 19 / (18 x 55) = 14.873737.
Q8_Q28_UNAVAILABLE_EXPECTED = {
    Q28_BOTTOM: dict(value="2.894737"),
    Q28_RATIO: dict(value="14.873737"),
    Q28_VERDICT: dict(result="supported"),
    GAP_LOW: dict(value=None),
    GAP_HIGH: dict(value=None),
    RATIO_LOW: dict(value=None),
    RATIO_HIGH: dict(value=None),
}

# The same rows with the usable-draw minimum at exactly the usable count: both
# intervals are available at their boundary. A twice is the gap's high end
# (0.322840; the observed gap is negative, B's tied demand errors sitting
# lowest while its carbon errors spread over the order) and the ratio's low
# end (7.952153). The ratio's high end is an A-with-B draw whose tie order
# puts B10 (0) in quintile 1 in place of A10 (10): 775 x 19 / (18 x 45) =
# 18.179012. In the observed sample A10 falls in quintile 1 (14.873737).
Q8_Q28_BOUNDARY_EXPECTED = {
    GAP_HIGH: dict(value="0.322840"),
    RATIO_LOW: dict(value="7.952153"),
    RATIO_HIGH: dict(value="18.179012"),
}

# The shortfall of periods 1 to 10 at 0: not applicable, no ratio, no interval.
Q28_NOT_APPLICABLE_EXPECTED = {
    Q28_BOTTOM: dict(value="0.000000"),
    Q28_TOP: dict(value="43.055556"),
    Q28_DIFFERENCE: dict(value="43.055556"),
    Q28_RATIO: dict(value=None),
    Q28_VERDICT: dict(result="not applicable: bottom quintile mean is 0 or less"),
    RATIO_DRAWS: dict(value=None, result="not applicable"),
    RATIO_LOW: dict(value=None),
    RATIO_HIGH: dict(value=None),
}

# The shortfall of periods 1 to 10 at 1 and of 39 to 48 at 1.25: the 19
# bottom half-hours average 1 and the 18 top ones 1.25, supported at the
# boundary; at 1.24, not supported.
Q28_BOUNDARY_EXPECTED = {
    Q28_BOTTOM: dict(value="1.000000"),
    Q28_TOP: dict(value="1.250000"),
    Q28_RATIO: dict(value="1.250000"),
    Q28_VERDICT: dict(result="supported"),
}
Q28_BELOW_BOUNDARY_EXPECTED = {
    Q28_RATIO: dict(value="1.240000"),
    Q28_VERDICT: dict(result="not supported"),
}

# No demand error at all: no matched day, and every result is withheld.
Q8_Q28_EMPTY_EXPECTED = {
    HISTORY_POPULATION: dict(half_hours=0, days=0, first_date=None),
    RHO_DEMAND: dict(half_hours=0, value=None),
    Q8_VERDICT: dict(result="no matched days: no result"),
    GAP_DRAWS: dict(value=None, result="not applicable"),
    Q28_BOTTOM: dict(value=None),
    Q28_VERDICT: dict(result="no matched days: no result"),
    RATIO_DRAWS: dict(value=None, result="not applicable"),
    DEMAND_HALF_HOURS: dict(half_hours=0),
    NOT_MATCHED: dict(days=360),
}

Q29 = "q29_final_forecast_error_by_month_and_block"
# The window runs from the 50-period day 26 October 2025 to the 46-period day
# 29 March 2026: 155 days, 7,440 half-hours, UTC 25 October 23:00 to
# 29 March 23:00, so exactly 1,860 in each UTC block. Seven errors fall
# inside it (+2, -5, 0, +3, +7, -1, +4); one row has no final forecast; one
# row lies on each side of the window, each with a large error.
Q29_FIXTURES = """
create temporary table synthetic_q29_outturn (
    period_start timestamptz,
    actual integer,
    api_final_forecast integer
) on commit drop;
insert into synthetic_q29_outturn values
    ('2025-10-25 22:30+00', 140, 100),
    ('2025-10-26 00:30+00', 102, 100),
    ('2025-10-26 01:30+00', 95, 100),
    ('2025-10-26 05:30+00', 100, 100),
    ('2025-10-26 06:00+00', 103, 100),
    ('2025-11-15 12:00+00', 107, 100),
    ('2025-11-15 18:00+00', 99, 100),
    ('2026-01-10 10:00+00', 200, null),
    ('2026-03-29 22:30+00', 104, 100),
    ('2026-03-29 23:00+00', 70, 100);
"""
Q29_TABLES = {"int_carbon_outturn__latest": "pg_temp.synthetic_q29_outturn"}
Q29_COLUMNS = (
    "half_hours",
    "half_hours_with_error",
    "half_hours_without_error",
    "mean_absolute_error",
    "mean_error",
    "median_error",
    "zero_error_share",
)
NO_ERRORS = (None, None, None, None)


def q29_cell(half_hours, with_error, statistics):
    values = (half_hours, with_error, half_hours - with_error, *statistics)
    return dict(zip(Q29_COLUMNS, values, strict=True))


# Overall: |errors| sum 22 over 7; signed sum 10 over 7; sorted -5, -1, 0, 2,
# 3, 4, 7 has median 2; one zero in 7. October (26 to 31: 50 + 5 x 48 = 290
# half-hours): +2, -5, 0, +3, an even-count median of 0 and 2. November: +7
# and -1. March (1 to 29: 28 x 48 + 46 = 1,390): +4. Blocks: 00:00-05:59
# holds +2, -5, 0; 06:00-11:59 holds +3 (and the row with no forecast);
# 12:00-17:59 holds +7; 18:00-23:59 holds -1 and +4.
Q29_EXPECTED = {
    (
        1,
        "all half-hours (partly seen before the rule as forecast minus actual: mean +0.43, median 0)",
    ): q29_cell(7440, 7, ("3.143", "1.429", "2.000", "0.143")),
    (2, "settlement month 2025-10 (partial: 26 to 31)"): q29_cell(
        290, 4, ("2.500", "0.000", "1.000", "0.250")
    ),
    (2, "settlement month 2025-11"): q29_cell(
        1440, 2, ("4.000", "3.000", "3.000", "0.000")
    ),
    (2, "settlement month 2025-12"): q29_cell(1488, 0, NO_ERRORS),
    (2, "settlement month 2026-01"): q29_cell(1488, 0, NO_ERRORS),
    (2, "settlement month 2026-02"): q29_cell(1344, 0, NO_ERRORS),
    (2, "settlement month 2026-03 (partial: 1 to 29)"): q29_cell(
        1390, 1, ("4.000", "4.000", "4.000", "0.000")
    ),
    (3, "UTC block 00:00-05:59"): q29_cell(
        1860, 3, ("2.333", "-1.000", "0.000", "0.333")
    ),
    (3, "UTC block 06:00-11:59"): q29_cell(
        1860, 1, ("3.000", "3.000", "3.000", "0.000")
    ),
    (3, "UTC block 12:00-17:59"): q29_cell(
        1860, 1, ("7.000", "7.000", "7.000", "0.000")
    ),
    (3, "UTC block 18:00-23:59"): q29_cell(
        1860, 2, ("2.500", "1.500", "1.500", "0.000")
    ),
}

# Q30 runs on the raw-shaped rows below through the compiled
# int_wind_error_by_period (a temporary view over them), with the add-back
# read from the shortfall and cohort rows, so the pairing, the add-back and
# the analysis are worked together. The window is settlement
# dates C 25 October 2025 (48 periods), A 26 October (the 50-period day,
# starting 23:00 UTC on the 25th) and B 27 October (48 periods, GMT): 146
# half-hours. Times below are UTC.
#
# Population, nine half-hours (carbon error e = actual - 200; forecast F from
# the latest issue before the half-hour; outturn O; add-back K; wind error
# W = O + K - F; delivered error D = O - F):
#   A1 00:00 (period 3, the first 01:00 local) e +3, F 5000 (issue 23:00 on
#      the 25th, 1 h old), O 4600: D = W = -400
#   A2 01:00 (period 5, the second 01:00 local) e -7, F 6000 (same issue,
#      2 h old), O 6700: D = W = +700
#   A3 01:30 e +1, the same issue's 01:00 point 6000 (2.5 h old), O 5950:
#      D = W = -50. A2 and A3 share one point.
#   A4 06:00 e +9, F 7000 from the 02:00 issue (4 h old): an issue stamped
#      exactly 06:00, whose 06:00 point is 100, is not before it. O 6300,
#      D -700; wind unit W1's 50 MWh adds K = +100 (gas unit G1's 500 MWh is
#      left out): W = -600. A reversed add-back would give -800.
#   A5 12:30 e -5, F 4000 from the 06:00 issue (6.5 h old), O 4200: +200
#   B1 03:00 e +2, F 3000 (0.5 h old), O 2800: -200
#   B2 09:30 e -6, F 3500 (1 h old), O 3700, D +200; W1 100 and W2 50 MWh
#      add K = +300: W = +500
#   B3 14:00 e +8, F 8000 (1.5 h old), first O 7200 (a later 9000 is not
#      used): -800
#   B4 20:30 e -4, F 2500 (1 h old), O 2840, D +340; W1 30 and W2 -50 MWh
#      give K = -40 (unit N1, outside the cohort, is left out): W = +300
# Left out, by first reason: 133 half-hours with no carbon error; B 04:30
# (no outturn); C 23:00 on the 24th (the only earlier issue is stamped at
# exactly its start); C 13:30 (the latest issue, 12:30, holds no 13:00
# point; an older one does); B 14:30 (W2 has an acceptance and a null
# deviation). 28 October 00:00, after the window, has every value and
# carbon error +50. Unit W3, uninstructed with a null deviation at A1, does
# not leave A1 out. A CCGT row at A1, published before its WIND row, is not
# used.
#
# Spearman with average ranks, pooled (n = 9, rank deviations from 5):
# abs(e) ranks are the values 1 to 9, sum of squares 60. abs(W) ranks A3 1,
# B1 and A5 2.5 (tied at 200), B4 4, A1 5, B2 6, A4 7, A2 8, B3 9: sum of
# squares 59.5, cross sum 51.5, rho_abs = 51.5 / sqrt(60 x 59.5) = 0.861932.
# Signed W and e have no ties: sum of d^2 236, rho_signed = 1 - 236 / 120 =
# -0.966667. abs(D) ties A5, B1, B2 (200, rank 3) and A2, A4 (700, rank
# 7.5): cross sum 45, squares 57.5, 45 / sqrt(60 x 57.5) = 0.766131. Signed
# D ties A5 and B2 (6.5): cross sum -55.5, squares 59.5, -55.5 /
# sqrt(60 x 59.5) = -0.928878. Minimum ranks would move every tied value.
#
# Bootstrap over the two population dates: with seed 20261027 a draw is A
# twice (249 draws), A and B (504) or B twice (247), so each interval runs
# from the lowest to the highest of A alone, the pooled value and B alone.
# A alone (n = 5): rho_abs 1 - 6 x 4 / 120 = 0.8, rho_signed -1, delivered
# rho_abs 8.5 / sqrt(10 x 9.5) = 0.872082, delivered rho_signed -1. B alone
# (n = 4): rho_abs 1, rho_signed -1, delivered rho_abs 3 / sqrt(5 x 4.5) =
# 0.632456, delivered rho_signed 1 - 6 x 18 / 60 = -0.8.
#
# Quintiles of abs(W), ntile(5) of 9 is 2, 2, 2, 2, 1. The tie key puts B1
# (03:00 on the 27th) before A5 (12:30 on the 26th), against period-start
# order, so quintile 1 is A3 and B1 (e +1, +2: mean abs 1.5, median 1.5),
# quintile 2 A5 and B4 (-5, -4), 3 A1 and B2 (+3, -6), 4 A4 and A2 (+9,
# -7), 5 B3 (+8).
Q30 = "q30_wind_vs_carbon_error"
Q30_BOOTSTRAP_SEED = 20261027
WIND_ERROR_MODEL = "int_wind_error_by_period"
Q30_FIXTURES = """
create temporary table synthetic_q30_outturn (
    period_start timestamptz,
    actual integer,
    api_final_forecast integer
) on commit drop;
create temporary table synthetic_windfor (
    start_time timestamptz,
    publish_time timestamptz,
    generation_mw integer,
    retrieved_at timestamptz
) on commit drop;
create temporary table synthetic_fuelhh (
    settlement_date date,
    settlement_period integer,
    start_time timestamptz,
    fuel_type text,
    publish_time timestamptz,
    generation_mw integer,
    retrieved_at timestamptz
) on commit drop;
create temporary table synthetic_wind_shortfall (
    national_grid_bm_unit text,
    settlement_date date,
    settlement_period integer,
    acceptance_count integer,
    instructed_deviation_mwh numeric
) on commit drop;
create temporary table synthetic_wind_cohort (
    national_grid_bm_unit text,
    fuel_group text
) on commit drop;

insert into synthetic_q30_outturn
select period_start::timestamptz, 200 + carbon_error, 200
from (
    values
        ('2025-10-26 00:00+00', 3),
        ('2025-10-26 01:00+00', -7),
        ('2025-10-26 01:30+00', 1),
        ('2025-10-26 06:00+00', 9),
        ('2025-10-26 12:30+00', -5),
        ('2025-10-27 03:00+00', {b_carbon_error[0]}),
        ('2025-10-27 09:30+00', {b_carbon_error[1]}),
        ('2025-10-27 14:00+00', {b_carbon_error[2]}),
        ('2025-10-27 20:30+00', {b_carbon_error[3]}),
        ('2025-10-27 04:30+00', 0),
        ('2025-10-24 23:00+00', 0),
        ('2025-10-25 13:30+00', 0),
        ('2025-10-27 14:30+00', 0),
        ('2025-10-28 00:00+00', 50)
) as errors (period_start, carbon_error)
where {with_carbon};

insert into synthetic_fuelhh
select settlement_date::date, settlement_period, start_time::timestamptz, fuel_type,
    start_time::timestamptz + interval '30 minutes' + publish_delay::interval, generation_mw,
    '2026-10-27 12:00+00'
from (
    values
        ('2025-10-25', 1, '2025-10-24 23:00+00', 'WIND', '0', 1000),
        ('2025-10-25', 30, '2025-10-25 13:30+00', 'WIND', '0', 1000),
        ('2025-10-26', 3, '2025-10-26 00:00+00', 'CCGT', '-10 minutes', 99999),
        ('2025-10-26', 3, '2025-10-26 00:00+00', 'WIND', '0', 4600),
        ('2025-10-26', 5, '2025-10-26 01:00+00', 'WIND', '0', 6700),
        ('2025-10-26', 6, '2025-10-26 01:30+00', 'WIND', '0', 5950),
        ('2025-10-26', 15, '2025-10-26 06:00+00', 'WIND', '0', 6300),
        ('2025-10-26', 28, '2025-10-26 12:30+00', 'WIND', '0', 4200),
        ('2025-10-27', 7, '2025-10-27 03:00+00', 'WIND', '0', 2800),
        ('2025-10-27', 20, '2025-10-27 09:30+00', 'WIND', '0', 3700),
        ('2025-10-27', 29, '2025-10-27 14:00+00', 'WIND', '0', 7200),
        ('2025-10-27', 29, '2025-10-27 14:00+00', 'WIND', '30 minutes', 9000),
        ('2025-10-27', 30, '2025-10-27 14:30+00', 'WIND', '0', 7900),
        ('2025-10-27', 42, '2025-10-27 20:30+00', 'WIND', '0', 2840),
        ('2025-10-28', 1, '2025-10-28 00:00+00', 'WIND', '0', 20000)
) as outturns (
    settlement_date, settlement_period, start_time, fuel_type, publish_delay, generation_mw
);

insert into synthetic_windfor
select start_time::timestamptz, publish_time::timestamptz, generation_mw, '2026-10-27 12:00+00'
from (
    values
        ('2025-10-24 23:00+00', '2025-10-24 23:00+00', 9999),
        ('2025-10-25 13:00+00', '2025-10-25 03:30+00', 7777),
        ('2025-10-25 12:00+00', '2025-10-25 12:30+00', 1100),
        ('2025-10-26 00:00+00', '2025-10-25 23:00+00', 5000),
        ('2025-10-26 01:00+00', '2025-10-25 23:00+00', 6000),
        ('2025-10-26 06:00+00', '2025-10-26 02:00+00', 7000),
        ('2025-10-26 06:00+00', '2025-10-26 06:00+00', 100),
        ('2025-10-26 12:00+00', '2025-10-26 06:00+00', 4000),
        ('2025-10-27 03:00+00', '2025-10-27 02:30+00', 3000),
        ('2025-10-27 09:00+00', '2025-10-27 08:30+00', 3500),
        ('2025-10-27 14:00+00', '2025-10-27 12:30+00', 8000),
        ('2025-10-27 20:00+00', '2025-10-27 19:30+00', 2500),
        ('2025-10-28 00:00+00', '2025-10-27 23:30+00', 1000)
) as points (start_time, publish_time, generation_mw);

insert into synthetic_wind_cohort values
    ('W1', 'wind'), ('W2', 'wind'), ('W3', 'wind'), ('G1', 'gas_turbine');

insert into synthetic_wind_shortfall values
    ('W1', '2025-10-26', 15, 1, 50),
    ('G1', '2025-10-26', 15, 1, 500),
    ('W3', '2025-10-26', 3, 0, null),
    ('W1', '2025-10-26', 3, 0, 0),
    ('W1', '2025-10-27', 20, 2, 100),
    ('W2', '2025-10-27', 20, 1, 50),
    ('W1', '2025-10-27', 42, 1, 30),
    ('W2', '2025-10-27', 42, 1, -50),
    ('N1', '2025-10-27', 42, 1, 999),
    ('W1', '2025-10-27', 30, 1, 20),
    ('W2', '2025-10-27', 30, 1, null),
    ('W1', '2025-10-28', 1, 1, 5000);
"""
Q30_B_CARBON_ERRORS = (2, -6, 8, -4)
Q30_CONSTANT_B_CARBON_ERRORS = (4, 4, 4, 4)
Q30_WIND_TABLES = {
    "stg_elexon__fuelhh": "pg_temp.synthetic_fuelhh",
    "stg_elexon__windfor": "pg_temp.synthetic_windfor",
}
Q30_TABLES = {
    "int_carbon_outturn__latest": "pg_temp.synthetic_q30_outturn",
    "int_wind_error_by_period": "pg_temp.synthetic_wind_error",
    "int_shortfall_by_unit_period": "pg_temp.synthetic_wind_shortfall",
    "int_elexon__bm_unit_cohort": "pg_temp.synthetic_wind_cohort",
}

PRIMARY_ERROR = "wind error, outturn with cohort curtailment add-back"
DELIVERED_ERROR = "wind error, delivered outturn"
Q30_POPULATION = (
    1,
    "population: window half-hours with error_api_final, a forecast, a first "
    "outturn and a known add-back",
    None,
)
Q30_READING = (
    1,
    "reading: describe only, no test, threshold or verdict; associated with, "
    "never caused by",
    None,
)
RHO_ABS = (2, "rho_abs (primary)", f"abs({PRIMARY_ERROR}) with abs(error_api_final)")
RHO_SIGNED = (
    3,
    "rho_signed (secondary)",
    f"{PRIMARY_ERROR}, with error_api_final; expected direction negative",
)
DELIVERED_RHO_ABS = (
    4,
    "rho_abs (sensitivity)",
    f"abs({DELIVERED_ERROR}) with abs(error_api_final)",
)
DELIVERED_RHO_SIGNED = (
    4,
    "rho_signed (sensitivity)",
    f"{DELIVERED_ERROR}, with error_api_final",
)
Q30_MEASURES = (RHO_ABS, RHO_SIGNED, DELIVERED_RHO_ABS, DELIVERED_RHO_SIGNED)
QUINTILE_MEAN = f"quintile of abs({PRIMARY_ERROR}): mean abs(error_api_final)"
QUINTILE_MEDIAN = f"quintile of abs({PRIMARY_ERROR}): median error_api_final"
QUINTILE_EDGE = (
    "half-hours sharing the quintile's largest abs(wind error) (value: that error, MW)"
)
LEFT_OUT_REASONS = (
    "no error_api_final",
    "no first FUELHH wind outturn",
    "no WINDFOR issue before the half-hour",
    "the latest issue holds no point for the hour",
    "add-back unknown: a wind unit with an acceptance has a null instructed deviation",
)
AGE_BANDS = ("at most 1 h", "over 1 h to 2 h", "over 2 h to 4 h", "over 4 h")
CLOCK_CHANGE_DATES = (
    "clock-change dates with {} periods, held whole (value: their window half-hours)"
)


def measure_key(measure, part=None):
    """The row key of a measure, or of one of its bootstrap rows."""

    section, name, description = measure
    if part is None:
        return (section, f"{name}: {description}", None)
    return (section, f"{name}: {part}", None)


def draws_part(usable_needed):
    return f"bootstrap draws with a value ({usable_needed} of {BOOTSTRAP_DRAWS} needed)"


def measure_cells(measure, rho, interval, usable_needed=900, usable_draws=1000):
    """A measure's four rows: its rho over the nine half-hours, the usable
    draws and an available interval (low, high)."""

    low, high = interval
    return {
        measure_key(measure): dict(half_hours=9, value=rho),
        measure_key(measure, draws_part(usable_needed)): dict(
            value=str(usable_draws), result="available"
        ),
        measure_key(measure, "bootstrap 90% interval, low"): dict(value=low),
        measure_key(measure, "bootstrap 90% interval, high"): dict(value=high),
    }


def quintile_cells(quintile, half_hours, mean, median, wind_range):
    return {
        (3, QUINTILE_MEAN, quintile): dict(
            half_hours=half_hours, value=mean, result=f"abs(wind error) {wind_range} MW"
        ),
        (3, QUINTILE_MEDIAN, quintile): dict(half_hours=half_hours, value=median),
    }


def left_out_key(reason_number):
    reason = LEFT_OUT_REASONS[reason_number - 1]
    return (5, f"left out, first reason {reason_number}: {reason}", None)


def age_band_key(band_number):
    band = AGE_BANDS[band_number - 1]
    return (
        5,
        f"population forecast age from the source stamp (stamp to half-hour start), band {band_number}: {band}",
        None,
    )


Q30_EXPECTED = {
    Q30_POPULATION: dict(
        half_hours=9, days=2, first_date="2025-10-26", last_date="2025-10-27"
    ),
    Q30_READING: dict(
        result="stated expectation, not a test: abs(rho) under about 0.2"
    ),
    **measure_cells(RHO_ABS, "0.861932", ("0.800000", "1.000000")),
    **measure_cells(RHO_SIGNED, "-0.966667", ("-1.000000", "-0.966667")),
    **measure_cells(DELIVERED_RHO_ABS, "0.766131", ("0.632456", "0.872082")),
    **measure_cells(DELIVERED_RHO_SIGNED, "-0.928878", ("-1.000000", "-0.800000")),
    **quintile_cells(1, 2, "1.500000", "1.500000", "50 to 200"),
    **quintile_cells(2, 2, "4.500000", "-4.500000", "200 to 300"),
    **quintile_cells(3, 2, "4.500000", "-1.500000", "400 to 500"),
    **quintile_cells(4, 2, "8.000000", "1.000000", "600 to 700"),
    **quintile_cells(5, 1, "8.000000", "8.000000", "800 to 800"),
    # B1 and A5 share quintile 1's largest abs(wind error), 200; it holds B1.
    (3, QUINTILE_EDGE, 1): dict(
        half_hours=2, value="200", result="in this quintile: 1"
    ),
    (3, QUINTILE_EDGE, 2): dict(
        half_hours=1, value="300", result="in this quintile: 1"
    ),
    (3, QUINTILE_EDGE, 3): dict(
        half_hours=1, value="500", result="in this quintile: 1"
    ),
    (3, QUINTILE_EDGE, 4): dict(
        half_hours=1, value="700", result="in this quintile: 1"
    ),
    (5, "settlement dates in the window", None): dict(
        half_hours=146, days=3, first_date="2025-10-25", last_date="2025-10-27"
    ),
    left_out_key(1): dict(half_hours=133),
    left_out_key(2): dict(half_hours=1),
    left_out_key(3): dict(half_hours=1),
    left_out_key(4): dict(half_hours=1),
    left_out_key(5): dict(half_hours=1),
    (5, "settlement dates with fewer than 46 population half-hours", None): dict(
        half_hours=9, days=3
    ),
    (5, CLOCK_CHANGE_DATES.format(46), None): dict(half_hours=0, days=0, value="0"),
    (5, CLOCK_CHANGE_DATES.format(50), None): dict(half_hours=5, days=1, value="50"),
    (5, "population half-hours with a non-zero add-back", None): dict(half_hours=3),
    (
        5,
        "wind unit-periods with an acceptance and a null instructed deviation, in "
        "window half-hours with an outturn (value: unit-periods)",
        None,
    ): dict(value="1"),
    (
        5,
        "wind unit-periods with a null instructed deviation, with or without an "
        "acceptance, in window half-hours with an outturn (value: unit-periods)",
        None,
    ): dict(value="2"),
    # A1, B1, B2 and B4 (1 h counts as at most 1 h); A2 (exactly 2 h) and B3;
    # A3 and A4 (exactly 4 h); A5.
    age_band_key(1): dict(half_hours=4),
    age_band_key(2): dict(half_hours=2),
    age_band_key(3): dict(half_hours=2),
    age_band_key(4): dict(half_hours=1),
}

# B's carbon errors all +4: a draw of B twice has a constant carbon measure
# and forms no rho, so 1,000 - 247 = 753 draws are usable for every rho,
# under 900. Pooled, abs(e) ranks become A3 1, A1 2, B 4.5 (four tied), A5
# 7, A2 8, A4 9: squares 55, cross sum with abs(W) 27.25, rho_abs = 27.25 /
# sqrt(55 x 59.5) = 0.476351. With 753 usable draws needed, the intervals
# are available: rho_abs runs from this pooled value to A alone's 0.8.
Q30_CONSTANT_B_RHO_ABS = "0.476351"


def q30_usable_draws_without_b_twice():
    return BOOTSTRAP_DRAWS - draws_without_day_one(2, Q30_BOOTSTRAP_SEED)


def q30_unavailable_expected(usable_draws):
    expected = {measure_key(RHO_ABS): dict(value=Q30_CONSTANT_B_RHO_ABS)}
    for measure in Q30_MEASURES:
        expected[measure_key(measure, draws_part(900))] = dict(
            value=str(usable_draws), result="unavailable"
        )
        expected[measure_key(measure, "bootstrap 90% interval, low")] = dict(value=None)
        expected[measure_key(measure, "bootstrap 90% interval, high")] = dict(
            value=None
        )
    return expected


def q30_boundary_expected(usable_draws):
    expected = {}
    for measure in Q30_MEASURES:
        expected[measure_key(measure, draws_part(usable_draws))] = dict(
            value=str(usable_draws), result="available"
        )
    expected[measure_key(RHO_ABS, "bootstrap 90% interval, low")] = dict(
        value=Q30_CONSTANT_B_RHO_ABS
    )
    expected[measure_key(RHO_ABS, "bootstrap 90% interval, high")] = dict(
        value="0.800000"
    )
    return expected


def q30_empty_expected():
    """No carbon error at all: no population, and every measure withheld."""

    expected = {
        Q30_POPULATION: dict(half_hours=0, days=0, first_date=None),
        left_out_key(1): dict(half_hours=146),
        (3, QUINTILE_MEAN, 1): dict(half_hours=None, value=None),
    }
    for measure in Q30_MEASURES:
        expected[measure_key(measure)] = dict(half_hours=0, value=None)
        expected[measure_key(measure, draws_part(900))] = dict(
            value=None, result="not applicable"
        )
    return expected


SESSION_TIME_ZONES = ("UTC", "Europe/London")


def q8_q28_fixtures(
    demand_blocks=MAIN_DEMAND_BLOCKS,
    bottom_shortfall="null",
    top_shortfall="null",
    constant_b=False,
    with_demand=True,
    constant_shortfall="null",
):
    """Q8 and Q28's rows. A shortfall of "null" keeps each half-hour's place."""

    return Q8_Q28_FIXTURES.format(
        demand_blocks=demand_blocks,
        bottom_shortfall=bottom_shortfall,
        top_shortfall=top_shortfall,
        constant_b=str(constant_b).lower(),
        with_demand=str(with_demand).lower(),
        constant_shortfall=constant_shortfall,
    )


def history_usable_draws():
    return usable_draws_without_b_twice(HISTORY_BOOTSTRAP_SEED)


def usable_draws_without_b_twice(seed):
    return BOOTSTRAP_DRAWS - draws_without_day_one(2, seed)


def picked_day(draw, pick, days, seed):
    """The day a bootstrap pick takes, worked out from the contract's rule."""

    digest = hashlib.md5(f"{seed}:{draw}:{pick}".encode()).hexdigest()
    return 1 + int(digest[:8], 16) % days


def draws_without_day_one(days, seed):
    """How many draws pick only days other than the first."""

    count = 0
    for draw in range(1, BOOTSTRAP_DRAWS + 1):
        picks = [picked_day(draw, pick, days, seed) for pick in range(1, days + 1)]
        if 1 not in picks:
            count += 1
    return count


# The contract's worked picks (computed on 31 October 2026 with .NET MD5): the
# first 8 hex digits of md5('<seed>:1:1'), and with two dates the draws that
# take the first date twice, one of each and the second twice.
KNOWN_PICKS = (
    ("q1-2026-10-31", "a14b7f81", (238, 515, 247)),
    ("q10-2026-10-31", "d5825519", (263, 473, 264)),
)


def two_date_draw_counts(seed):
    first_twice = 0
    one_each = 0
    second_twice = 0
    for draw in range(1, BOOTSTRAP_DRAWS + 1):
        first_pick = picked_day(draw, 1, 2, seed)
        second_pick = picked_day(draw, 2, 2, seed)
        if first_pick == second_pick == 1:
            first_twice += 1
        elif first_pick == second_pick == 2:
            second_twice += 1
        else:
            one_each += 1
    return (first_twice, one_each, second_twice)


def known_pick_mismatches():
    """picked_day agrees with the contract's worked picks."""

    found = []
    for seed, first_hex, counts in KNOWN_PICKS:
        digest = hashlib.md5(f"{seed}:1:1".encode()).hexdigest()
        if digest[:8] != first_hex:
            found.append(f"picks {seed}: md5 begins {digest[:8]}, expected {first_hex}")
        if picked_day(1, 1, 2, seed) != 2:
            found.append(
                f"picks {seed}: draw 1's first pick of two dates is not date 2"
            )
        if two_date_draw_counts(seed) != counts:
            found.append(
                f"picks {seed}: two-date draws {two_date_draw_counts(seed)}, "
                f"expected {counts}"
            )
    return found


def draw_statistics(date_values, seed, statistic):
    """Each bootstrap draw's statistic over the values of the dates it picks.

    date_values lists each date's half-hour values, dates in ascending order;
    a draw takes as many dates as there are, with replacement. A draw that
    picks no value has no statistic and is left out.
    """

    dates = len(date_values)
    statistics = []
    for draw in range(1, BOOTSTRAP_DRAWS + 1):
        picked_values = []
        for pick in range(1, dates + 1):
            picked_values += date_values[picked_day(draw, pick, dates, seed) - 1]
        if not picked_values:
            continue
        statistics.append(statistic(picked_values))
    return statistics


def mean_of(values):
    return Fraction(sum(values), len(values))


def mean_absolute_of(values):
    total = 0
    for value in values:
        total += abs(value)
    return Fraction(total, len(values))


def percentile_cont(values, fraction):
    """PostgreSQL's percentile_cont: linear between the two nearest ranks."""

    ordered = sorted(values)
    position = fraction * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def rounded(value, places):
    """An exact value as round(numeric, places) shows it: half away from zero."""

    exact = Decimal(value.numerator) / Decimal(value.denominator)
    return str(exact.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP))


def six_decimals(value):
    return rounded(value, 6)


def three_decimals(value):
    return rounded(value, 3)


def on_fixtures(manifest, analysis, tables):
    """The analysis's compiled SQL reading the temporary tables instead."""

    return compiled_on_fixtures(manifest, f"analysis.gridskew.{analysis}", tables)


def compiled_on_fixtures(manifest, node_id, tables):
    """A compiled node's SQL reading the temporary tables instead."""

    node = manifest["nodes"][node_id]
    sql = node["compiled_code"]
    for relation_id in node["depends_on"]["nodes"]:
        if relation_id.startswith("source."):
            relation = manifest["sources"][relation_id]
        else:
            relation = manifest["nodes"][relation_id]
        if relation["name"] not in tables:
            raise SystemExit(
                f"{node['name']} reads {relation['name']}, which has no fixture"
            )
        sql = sql.replace(relation["relation_name"], tables[relation["name"]])
    return sql


def readable(value):
    """Values as text, instants in UTC whatever the session's time zone."""

    if isinstance(value, datetime):
        return str(value.astimezone(timezone.utc))
    if isinstance(value, (Decimal, date)):
        return str(value)
    return value


def run_on_fixtures(conn, fixtures, query, time_zone, key_columns=None):
    """The rows keyed by (section, item, horizon or quintile), and any error.

    key_columns names other key columns for an analysis without an item
    column. The error is the database's message, or the first key that two
    rows share.
    """

    try:
        with conn.cursor() as cursor:
            cursor.execute(f"set time zone '{time_zone}'")
            cursor.execute(fixtures)
            cursor.execute(query)
            columns = [column.name for column in cursor.description]
            rows = cursor.fetchall()
    except psycopg.Error as error:
        return {}, str(error).strip()
    finally:
        conn.rollback()

    keyed = {}
    for row in rows:
        cells = dict(zip(columns, [readable(value) for value in row], strict=True))
        if key_columns is not None:
            key = tuple(cells[column] for column in key_columns)
        else:
            cell = None
            for cell_column in ("horizon", "quintile"):
                if cell_column in cells:
                    cell = cells[cell_column]
            key = (cells["section"], cells["item"], cell)
        if key in keyed:
            return keyed, f"two rows {key}"
        keyed[key] = cells
    return keyed, None


def mismatches(title, run, expected):
    rows, error = run
    if error is not None:
        return [f"{title}: {error}"]
    found = []
    for key, columns in expected.items():
        if key not in rows:
            found.append(f"{title}: no row {key}")
            continue
        for column, value in columns.items():
            if column not in rows[key]:
                found.append(f"{title}: {key} has no column {column}")
                continue
            actual = rows[key][column]
            if actual != value:
                found.append(
                    f"{title}: {key} {column} is {actual!r}, expected {value!r}"
                )
    return found


def unexpected_rows(title, run, expected):
    """Rows the worked values do not list: the expected keys are the whole output."""

    rows, error = run
    if error is not None:
        return []
    found = []
    for key in rows:
        if key not in expected:
            found.append(f"{title}: unexpected row {key}")
    return found


def gated_values_left_null(title, run, gated):
    """Every gated column is null in every row of the gated sections."""

    rows, error = run
    if error is not None:
        return []
    sections, columns = gated
    found = []
    for key, cells in rows.items():
        if cells["section"] not in sections:
            continue
        for column in columns:
            if column not in cells:
                found.append(f"{title}: {key} has no column {column}")
                continue
            if cells[column] is not None:
                found.append(
                    f"{title}: {key} {column} is {cells[column]!r} before the population"
                )
    return found


def columns_left_null(title, run, columns):
    """Every listed column is null in every row."""

    rows, error = run
    if error is not None:
        return []
    found = []
    for key, cells in rows.items():
        for column in columns:
            if column not in cells:
                found.append(f"{title}: {key} has no column {column}")
                continue
            if cells[column] is not None:
                found.append(
                    f"{title}: {key} {column} is {cells[column]!r} before the gate"
                )
    return found


def run_analysis(conn, fixtures, manifest, analysis, tables, time_zone):
    query = on_fixtures(manifest, analysis, tables)
    return run_on_fixtures(conn, fixtures, query, time_zone)


def run_one_row_analysis(conn, fixtures, manifest, analysis, tables, time_zone):
    """An analysis that returns one row, keyed by the empty tuple."""

    query = on_fixtures(manifest, analysis, tables)
    return run_on_fixtures(conn, fixtures, query, time_zone, key_columns=())


def q1_verdict_mismatches(conn, project_values, time_zone):
    title = f"[{time_zone}] Q1 verdict"
    found = []
    cases = (
        ("exact half weight", Q1_EXACT_HALF_ROWS, Q1_EXACT_HALF_EXPECTED),
        ("thresholds", Q1_THRESHOLD_ROWS, Q1_THRESHOLD_EXPECTED),
        ("part not seen", Q1_PART_NOT_SEEN_ROWS, Q1_PART_NOT_SEEN_EXPECTED),
    )
    for case_name, drift_rows, expected in cases:
        run = run_one_row_analysis(
            conn,
            q1_verdict_fixtures(drift_rows),
            project_values,
            Q1_VERDICT,
            Q1_VERDICT_TABLES,
            time_zone,
        )
        found += mismatches(f"{title} {case_name}", run, expected)
        second_run = run_one_row_analysis(
            conn,
            q1_verdict_fixtures(drift_rows),
            project_values,
            Q1_VERDICT,
            Q1_VERDICT_TABLES,
            time_zone,
        )
        if second_run != run:
            found.append(f"{title} {case_name}: a second run gives different rows")

    early_run = run_one_row_analysis(
        conn,
        q1_verdict_fixtures(Q1_THRESHOLD_ROWS, gate_open=False),
        project_values,
        Q1_VERDICT,
        Q1_VERDICT_TABLES,
        time_zone,
    )
    found += mismatches(f"{title} early", early_run, Q1_THRESHOLD_NOT_REACHED_EXPECTED)
    found += columns_left_null(f"{title} early", early_run, Q1_VERDICT_GATED)
    return found


def postgres_correlation(conn, pairs):
    """PostgreSQL's corr() over the pairs, to six decimals, as text."""

    rows = []
    for earlier, later in pairs:
        rows.append(f"({earlier}, {later})")
    with conn.cursor() as cursor:
        cursor.execute(
            f"select round(corr(earlier, later)::numeric, 6)::text "
            f"from (values {', '.join(rows)}) as pairs (earlier, later)"
        )
        (correlation,) = cursor.fetchone()
    conn.rollback()
    return correlation


def q1_beside_mismatches(conn, project_values, time_zone):
    title = f"[{time_zone}] Q1 beside"
    found = []
    three_dates_expected = q1_model_mean_expected(Q1_THREE_DATES)
    twelve_dates_expected = q1_model_mean_expected(Q1_TWELVE_DATES)
    cases = (
        ("alternating revisions", Q1_ALTERNATING, Q1_ALTERNATING_EXPECTED),
        ("zeros kept", Q1_ZEROS_KEPT, Q1_ZEROS_KEPT_EXPECTED),
        ("two dates", Q1_TWO_DATES, Q1_TWO_DATES_EXPECTED),
        ("three dates", Q1_THREE_DATES, three_dates_expected),
        ("twelve dates", Q1_TWELVE_DATES, twelve_dates_expected),
    )
    for case_name, half_hours, expected in cases:
        run = run_analysis(
            conn,
            q1_beside_fixtures(half_hours),
            project_values,
            Q1_BESIDE,
            Q1_BESIDE_TABLES,
            time_zone,
        )
        found += mismatches(f"{title} {case_name}", run, expected)
        second_run = run_analysis(
            conn,
            q1_beside_fixtures(half_hours),
            project_values,
            Q1_BESIDE,
            Q1_BESIDE_TABLES,
            time_zone,
        )
        if second_run != run:
            found.append(f"{title} {case_name}: a second run gives different rows")

    correlation = postgres_correlation(conn, Q1_ZEROS_KEPT_PAIRS)
    if correlation != Q1_ZEROS_KEPT_EXPECTED[R_ROW]["value"]:
        found.append(f"{title}: corr() of the zeros-kept pairs is {correlation}")

    early_run = run_analysis(
        conn,
        q1_beside_fixtures(Q1_TWO_DATES, gate_open=False),
        project_values,
        Q1_BESIDE,
        Q1_BESIDE_TABLES,
        time_zone,
    )
    found += mismatches(f"{title} early", early_run, Q1_TWO_DATES_EARLY_EXPECTED)
    found += gated_values_left_null(f"{title} early", early_run, Q1_BESIDE_GATED)
    return found


def q10_mismatches(conn, reached, not_reached, time_zone):
    title = f"[{time_zone}] Q10"
    expected = with_model_intervals(
        Q10_OBSERVED_EXPECTED, Q10_CELL_ERRORS, Q10_DAY_BEFORE_LABELS
    )
    main_run = run_analysis(conn, q10_fixtures(), reached, Q10, Q10_TABLES, time_zone)
    early_run = run_analysis(
        conn, q10_fixtures(), not_reached, Q10, Q10_TABLES, time_zone
    )
    found = mismatches(title, main_run, expected)
    found += unexpected_rows(title, main_run, expected)
    second_run = run_analysis(conn, q10_fixtures(), reached, Q10, Q10_TABLES, time_zone)
    if second_run != main_run:
        found.append(f"{title}: a second run gives different rows")
    found += mismatches(f"{title} early", early_run, Q10_NOT_REACHED_EXPECTED)
    found += gated_values_left_null(f"{title} early", early_run, Q10_GATED)

    short_look_ahead_run = run_analysis(
        conn, q10_fixtures(third_poll=""), reached, Q10, Q10_TABLES, time_zone
    )
    found += gated_values_left_null(
        f"{title} two polls after the cutoff", short_look_ahead_run, Q10_GATED
    )

    twelve_dates_run = run_analysis(
        conn,
        q10_fixtures(error_rows=q10_twelve_dates_rows(), outturn_rows=""),
        reached,
        Q10,
        Q10_TABLES,
        time_zone,
    )
    found += mismatches(
        f"{title} twelve dates",
        twelve_dates_run,
        with_model_intervals(
            Q10_TWELVE_DATES_EXPECTED, q10_twelve_dates_cell_errors(), {}
        ),
    )
    return found


def q10_project_values_mismatches(conn, project_values, time_zone):
    title = f"[{time_zone}] Q10 project values"
    run = run_analysis(
        conn, q10_project_values_fixtures(), project_values, Q10, Q10_TABLES, time_zone
    )
    found = mismatches(title, run, Q10_PROJECT_VALUES_EXPECTED)
    early_run = run_analysis(
        conn,
        q10_project_values_fixtures(gate_open=False),
        project_values,
        Q10,
        Q10_TABLES,
        time_zone,
    )
    found += gated_values_left_null(f"{title} early", early_run, Q10_GATED)
    return found


def q25_mismatches(conn, reached, time_zone):
    query = on_fixtures(reached, Q25, Q25_TABLES)
    run = run_on_fixtures(conn, Q25_FIXTURES, query, time_zone, Q25_KEY)
    return mismatches(f"[{time_zone}] Q25", run, Q25_EXPECTED)


def q37_mismatches(conn, reached, time_zone):
    title = f"[{time_zone}] Q37"
    query = on_fixtures(reached, Q37, Q37_TABLES)
    run = run_on_fixtures(conn, Q37_FIXTURES, query, time_zone, Q37_KEY)
    found = mismatches(title, run, Q37_EXPECTED)
    found += unexpected_rows(title, run, Q37_EXPECTED)
    return found


def q38_mismatches(conn, reached, time_zone):
    title = f"[{time_zone}] Q38"
    query = on_fixtures(reached, Q38, Q38_TABLES)
    run = run_on_fixtures(conn, Q38_FIXTURES, query, time_zone, Q38_KEY)
    found = mismatches(title, run, Q38_EXPECTED)
    found += unexpected_rows(title, run, Q38_EXPECTED)
    return found


def q8_q28_mismatches(conn, reached, boundary, time_zone):
    title = f"[{time_zone}] Q8 and Q28"
    main_run = run_analysis(
        conn, q8_q28_fixtures(), reached, Q8_Q28, Q8_Q28_TABLES, time_zone
    )
    found = mismatches(title, main_run, Q8_Q28_EXPECTED)
    found += unexpected_rows(title, main_run, Q8_Q28_EXPECTED)

    for demand_blocks, rho_demand, gap, verdict in Q8_GAP_CASES:
        gap_run = run_analysis(
            conn,
            q8_q28_fixtures(demand_blocks),
            reached,
            Q8_Q28,
            Q8_Q28_TABLES,
            time_zone,
        )
        found += mismatches(
            f"{title} demand blocks {demand_blocks}",
            gap_run,
            {
                RHO_DEMAND: dict(value=rho_demand),
                RHO_SHORTFALL: Q8_Q28_EXPECTED[RHO_SHORTFALL],
                GAP: dict(value=gap),
                Q8_VERDICT: dict(result=verdict),
            },
        )

    usable_draws = history_usable_draws()
    unavailable_expected = dict(Q8_Q28_UNAVAILABLE_EXPECTED)
    unavailable_expected[GAP_DRAWS] = dict(
        value=str(usable_draws), result="unavailable"
    )
    unavailable_expected[RATIO_DRAWS] = dict(
        value=str(usable_draws), result="unavailable"
    )
    unavailable_run = run_analysis(
        conn,
        q8_q28_fixtures(constant_b=True),
        reached,
        Q8_Q28,
        Q8_Q28_TABLES,
        time_zone,
    )
    found += mismatches(f"{title} unavailable", unavailable_run, unavailable_expected)

    needed = f"({usable_draws} of {BOOTSTRAP_DRAWS} needed)"
    boundary_expected = dict(Q8_Q28_BOUNDARY_EXPECTED)
    boundary_expected[(2, f"bootstrap draws with a gap {needed}", None)] = dict(
        value=str(usable_draws), result="available"
    )
    boundary_expected[(3, f"bootstrap draws with a ratio {needed}", None)] = dict(
        value=str(usable_draws), result="available"
    )
    boundary_run = run_analysis(
        conn,
        q8_q28_fixtures(constant_b=True),
        boundary,
        Q8_Q28,
        Q8_Q28_TABLES,
        time_zone,
    )
    found += mismatches(f"{title} boundary", boundary_run, boundary_expected)

    not_applicable_run = run_analysis(
        conn,
        q8_q28_fixtures(bottom_shortfall="0"),
        reached,
        Q8_Q28,
        Q8_Q28_TABLES,
        time_zone,
    )
    found += mismatches(
        f"{title} not applicable", not_applicable_run, Q28_NOT_APPLICABLE_EXPECTED
    )

    ratio_boundary_run = run_analysis(
        conn,
        q8_q28_fixtures(bottom_shortfall="1", top_shortfall="1.25"),
        reached,
        Q8_Q28,
        Q8_Q28_TABLES,
        time_zone,
    )
    found += mismatches(
        f"{title} ratio at 1.25", ratio_boundary_run, Q28_BOUNDARY_EXPECTED
    )
    below_boundary_run = run_analysis(
        conn,
        q8_q28_fixtures(bottom_shortfall="1", top_shortfall="1.24"),
        reached,
        Q8_Q28,
        Q8_Q28_TABLES,
        time_zone,
    )
    found += mismatches(
        f"{title} ratio at 1.24", below_boundary_run, Q28_BELOW_BOUNDARY_EXPECTED
    )

    constant_shortfall_run = run_analysis(
        conn,
        q8_q28_fixtures(constant_shortfall="7"),
        reached,
        Q8_Q28,
        Q8_Q28_TABLES,
        time_zone,
    )
    found += mismatches(
        f"{title} constant shortfall",
        constant_shortfall_run,
        Q8_Q28_CONSTANT_SHORTFALL_EXPECTED,
    )

    empty_run = run_analysis(
        conn,
        q8_q28_fixtures(with_demand=False),
        reached,
        Q8_Q28,
        Q8_Q28_TABLES,
        time_zone,
    )
    found += mismatches(f"{title} no demand", empty_run, Q8_Q28_EMPTY_EXPECTED)
    return found


def q29_mismatches(conn, reached, time_zone):
    title = f"[{time_zone}] Q29"
    query = on_fixtures(reached, Q29, Q29_TABLES)
    run = run_on_fixtures(
        conn, Q29_FIXTURES, query, time_zone, key_columns=("section", "item")
    )
    found = mismatches(title, run, Q29_EXPECTED)
    found += unexpected_rows(title, run, Q29_EXPECTED)
    return found


def q30_fixtures(reached, b_carbon_errors=Q30_B_CARBON_ERRORS, with_carbon=True):
    """Q30's rows, and int_wind_error_by_period as a temporary view over them."""

    wind_error_view = compiled_on_fixtures(
        reached, f"model.gridskew.{WIND_ERROR_MODEL}", Q30_WIND_TABLES
    )
    tables = Q30_FIXTURES.format(
        b_carbon_error=b_carbon_errors, with_carbon=str(with_carbon).lower()
    )
    return (
        f"{tables}\ncreate temporary view synthetic_wind_error as\n"
        f"{wind_error_view.rstrip()}\n;"
    )


def q30_mismatches(conn, reached, boundary, time_zone):
    title = f"[{time_zone}] Q30"
    main_run = run_analysis(
        conn, q30_fixtures(reached), reached, Q30, Q30_TABLES, time_zone
    )
    found = mismatches(title, main_run, Q30_EXPECTED)
    found += unexpected_rows(title, main_run, Q30_EXPECTED)

    usable_draws = q30_usable_draws_without_b_twice()
    constant_b = q30_fixtures(reached, Q30_CONSTANT_B_CARBON_ERRORS)
    unavailable_run = run_analysis(
        conn, constant_b, reached, Q30, Q30_TABLES, time_zone
    )
    found += mismatches(
        f"{title} unavailable", unavailable_run, q30_unavailable_expected(usable_draws)
    )
    boundary_run = run_analysis(conn, constant_b, boundary, Q30, Q30_TABLES, time_zone)
    found += mismatches(
        f"{title} boundary", boundary_run, q30_boundary_expected(usable_draws)
    )

    empty_run = run_analysis(
        conn,
        q30_fixtures(reached, with_carbon=False),
        reached,
        Q30,
        Q30_TABLES,
        time_zone,
    )
    found += mismatches(f"{title} no carbon error", empty_run, q30_empty_expected())
    return found


def check_all(reached, not_reached, boundary, project_values):
    """Every mismatch between the analyses on the fixtures and the worked values.

    reached, not_reached, boundary and project_values are manifests compiled
    with REACHED_VARS, NOT_REACHED_VARS, boundary_vars() and
    PROJECT_VALUES_VARS. Each case runs with the connection's time zone set
    to UTC and to Europe/London.
    """

    conn = disposable_connection()
    found = known_pick_mismatches()
    try:
        for time_zone in SESSION_TIME_ZONES:
            found += q1_verdict_mismatches(conn, project_values, time_zone)
            found += q1_beside_mismatches(conn, project_values, time_zone)
            found += q10_mismatches(conn, reached, not_reached, time_zone)
            found += q10_project_values_mismatches(conn, project_values, time_zone)
            found += q25_mismatches(conn, reached, time_zone)
            found += q37_mismatches(conn, reached, time_zone)
            found += q38_mismatches(conn, reached, time_zone)
            found += q8_q28_mismatches(conn, reached, boundary, time_zone)
            found += q29_mismatches(conn, reached, time_zone)
            found += q30_mismatches(conn, reached, boundary, time_zone)
    finally:
        conn.close()
    return found


def boundary_vars():
    """REACHED_VARS with the Q8 and Q28 and Q30's usable-draw minimums at
    their fixtures' usable counts."""

    variables = dict(REACHED_VARS)
    variables["demand_history_bootstrap_min_usable_draws"] = history_usable_draws()
    variables["q30_bootstrap_min_usable_draws"] = q30_usable_draws_without_b_twice()
    return variables


def main():
    if (
        os.getenv("GRIDSKEW_DISPOSABLE_TEST") != "1"
        or os.getenv("DBT_HOST") not in {"localhost", "127.0.0.1"}
        or os.getenv("DBT_DBNAME") != "gridskew_dev"
    ):
        raise RuntimeError(
            "Analysis checks require opt-in to a local disposable gridskew_dev"
        )
    found = check_all(
        compile_analyses(REACHED_VARS, also_compiled=(WIND_ERROR_MODEL,)),
        compile_analyses(NOT_REACHED_VARS),
        compile_analyses(boundary_vars()),
        compile_analyses(PROJECT_VALUES_VARS),
    )
    for mismatch in found:
        print(mismatch)
    if found:
        raise SystemExit(f"{len(found)} analysis cells differ from the worked values")
    print(
        "Analyses: Q1, Q10, Q25, Q37, Q38, Q8, Q28, Q29 and Q30 match every worked value"
    )


if __name__ == "__main__":
    main()
