"""Check the one-look analyses (Q1, Q10, Q4, Q2-RF, Q25, Q27, Q8, Q28, Q29, Q30, Q26) on worked rows.

CI only runs analyses with limit 0, so their arithmetic is never exercised.
This compiles them with small populations, points each at temporary tables
of hand-worked rows instead of the relations it reads, and compares every
listed cell with the value worked out in the comments. It refuses to run
unless pointed at a local disposable gridskew_dev.

Run from the repository root with the dbt environment set, for example:
    GRIDSKEW_DISPOSABLE_TEST=1 DBT_HOST=localhost DBT_DBNAME=gridskew_dev ...
    python -m tests.adhoc.check_one_look_analyses
"""

import hashlib
import math
import os
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction

import psycopg

from tests.adhoc.check_analyses_compile import compile_analyses, disposable_connection

Q10 = "q10_forecast_accuracy_by_lead_time"
Q4 = "q4_forecast_error_vs_shortfall"
Q2RF = "q2rf_restatement_r3_to_rf"
Q25 = "q25_instruction_fragmentation"
Q27 = "q27_forecast_stalls"
READ_UNTIL = "2026-09-05 00:00:00+00"
# Q4's read time: B1610 rows stored at or before it count.
Q4_CAPTURE_CUTOFF = "2026-09-25 12:00:00+00"
STALLS_FROM = "2026-09-01 00:00:00+00"
STALLS_WINDOW_VARS = {
    "forecast_stalls_from": STALLS_FROM,
    "forecast_stalls_exploratory_from": "2026-08-25 05:00:00+00",
    "forecast_stalls_exploratory_until": "2026-08-25 06:30:00+00",
}
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
    "shortfall_error_matched_days_needed": 2,
    "shortfall_error_capture_cutoff": Q4_CAPTURE_CUTOFF,
    "rf_restatement_dates_needed": 2,
    # Not the project's 450, so a 450 typed into the SQL fails; the two
    # manifests' cutoffs differ, so a typed cutoff fails one of them.
    "b1610_rf_capture_offset_days": 449,
    "rf_restatement_capture_cutoff": "2026-11-29 12:00:00+00",
    "forecast_stalls_days_needed": 4,
    **STALLS_WINDOW_VARS,
    **Q29_WINDOW_VARS,
    **Q30_WINDOW_VARS,
}
NOT_REACHED_VARS = {
    "analysis_read_until": READ_UNTIL,
    "forecast_drift_half_hours_needed": 8,
    "shortfall_error_matched_days_needed": 3,
    "shortfall_error_capture_cutoff": Q4_CAPTURE_CUTOFF,
    "rf_restatement_capture_cutoff": "2026-11-30 00:00:00+00",
    "forecast_stalls_days_needed": 5,
    **STALLS_WINDOW_VARS,
}
# Q4 compiled without one of the read's two instants, the gate otherwise
# open: no result either way.
Q4_WITHOUT_READ_UNTIL_VARS = {
    "shortfall_error_matched_days_needed": 2,
    "shortfall_error_capture_cutoff": Q4_CAPTURE_CUTOFF,
}
Q4_WITHOUT_CAPTURE_CUTOFF_VARS = {
    "analysis_read_until": READ_UNTIL,
    "shortfall_error_matched_days_needed": 2,
}
# Q4's cutoff in B's last hour: 22:30 UTC is 23:30 on 2 September in London,
# B's own settlement date; 23:00 UTC is midnight, 3 September in London but
# still 2 September in UTC. B is the second matched day either way.
Q4_CUTOFF_ON_B_VARS = dict(REACHED_VARS, analysis_read_until="2026-09-02 22:30:00+00")
Q4_CUTOFF_AFTER_B_VARS = dict(
    REACHED_VARS, analysis_read_until="2026-09-02 23:00:00+00"
)
# The Q4 manifests compiled with other cutoffs, by name.
Q4_OTHER_CUTOFF_VARS = {
    "without analysis_read_until": Q4_WITHOUT_READ_UNTIL_VARS,
    "without the capture cutoff": Q4_WITHOUT_CAPTURE_CUTOFF_VARS,
    "cutoff on B's settlement date": Q4_CUTOFF_ON_B_VARS,
    "cutoff at the start of 3 September in London": Q4_CUTOFF_AFTER_B_VARS,
}
# The bootstrap's fixed seed and draw count, as in the Q4 analysis.
BOOTSTRAP_SEED = 20261012
BOOTSTRAP_DRAWS = 1000

# Cases on the project's own values of the rule boundaries that are vars (the
# part not seen after 2026-09-10 05:30 UTC, the poll schedule switch at slot
# 11:00 that day, Q27's record to 2026-10-31 23:30 UTC and its window from
# 1 November), with a cutoff later than all of them, so a mistyped var fails.
PROJECT_VALUES_VARS = {
    "analysis_read_until": "2026-11-02 00:00:00+00",
    "forecast_drift_half_hours_needed": 1,
    "forecast_stalls_days_needed": 1,
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


# Q4's days, settlement dates in summer time: A 1 September and B 2 September
# are matched (48 and 46 half-hours with both values); C 3 September has 45
# (not matched); E 6 September is after analysis_read_until. Every final
# forecast is exactly 30 minutes ahead (kept: the limit is inclusive) except
# C's first half-hour, 30 minutes and a second ahead (left out; C stays
# unmatched, so no other value moves). Every half-hour
# p (1 to 48) has error_final p when p is odd and -p when even, so the sorted
# abs(error_final) puts A's p at position 2p - 1 and B's at 2p for p up to 46,
# then A47 (93) and A48 (94). ntile(5) of 94: 19, 19, 19, 19, 18, so
# quintile 1 is A1 to A10 and B1 to B9 (A10 and B10 tie at the edge, broken by
# period start), quintile 5 is A39 to A48 and B39 to B46.
#
# The cohort measure is U1's value (U2's -5 adds nothing once its positive
# part is taken). U3 is outside the cohort and U4 is not determinable, both
# 100 in A's first half-hour. U5 adds a third determinable unit-period, 0, to
# A's second half-hour. A U1 value at 12:15 UTC on A matches no error row.
# B47 holds only a null U1 value and B48 nothing, so B has 46 half-hours with
# both values.
#
# The active cohort's window runs from A's first half-hour (23:00 UTC on 31
# August) to analysis_read_until (00:00 UTC on 5 September), both included.
# U1, U2, U4 and U5 each have a PN above 0 in it: active, so every value
# above stays as it is. The other cohort units:
#   IE1 (INTELE) and IF1 (INTFR): interconnectors whatever they did; each
#     has 100 MWh in a top-quintile half-hour that must not count;
#   IM1 only imports (PN 0, metered -2, and 0 in another half-hour): inactive;
#   P1 has a PN of 3 in the window's first half-hour only, a row the
#     shortfall table cannot judge: active;
#   M1 has no PN, and metered 0 then 2 (first and latest run) at the window's
#     last half-hour: active, with no PN unit-period;
#   F1 has PN 0 and metered 3 then 0: active on its first run;
#   BW1 is active only half an hour before the window, AW1 only half an hour
#     after the cutoff, and NR1 has no row: all inactive, none with a row in
#     the window;
#   EP1 and OP1 are active on a PN in the window; EP1's only shortfall row is
#     at the cutoff, OP1's only one half an hour before the window, neither
#     determinable.
# Ten active units (Y1, below, among them), two interconnectors (one INTELE),
# five inactive (Z1, below, and three with no row in the window); M1 and OP1
# have no PN unit-period in the window.
#
# Idle unit-periods behind the population half-hours: U5's (PN 0 and metered
# 0) is idle; U2's at A's third half-hour (PN 0, metered 1) is not, nor is
# U4's (PN 0 and metered 0 but not determinable). 94 half-hours of U1 and U2
# and U5's one make 189 determinable unit-periods: an idle share of 1 / 189.
#
# Settlement runs. Every B1610 decision reads the stored rows held at the
# capture cutoff, 25 September 12:00:00 UTC; synthetic_metered_periods is the
# current mapped view, which Q4 no longer reads (main's analysis does). Each
# unit-period's metered value is a stored B1610 row with the unit's own
# National Grid ID: SF, stored on 20 September, except A's U1 values, which
# are R1 rows stored on 24 September beside an earlier SF row 7 MWh higher,
# so A is partly on R1 and partly on SF (eligible) and the latest run held
# gives the value. A's U1 at period 4 has its SF row stored an hour after its
# R1 row (24 September 09:00): the run, not the storing time, decides. U1's
# metered value is its period number plus 100 times the day of the month, so
# a row read from another period or date gives another value. B's U1 at
# period 1 has a II row (4 MWh) from 9 September and its SF row stored
# exactly at the cutoff (held). A's U1 at period 3 has an RF row of 1,000 MWh
# stored a second after it (not held).
#
# Activity on metered output comes from the held rows too: M1's SF row of 0
# is stored before its R1 row of 2 (first 0, latest 2: active); F1's II row
# of 3 is stored before its SF row of 0 (active on its first row); IM1 holds
# -2 only. Z1 (CCGT) has PN 0 and an SF row of 0 at A's period 2: inactive,
# with a row in the window, so its determinable unit-period there is not read.
#
# Left out with an expected energy and no held row mapped to the unit, six:
#   U5 at C's period 20 (9 MWh): its only row is stored an hour after the
#     cutoff;
#   U5 at C's period 30 (8 MWh): its II row (0 MWh, U5) is followed by an SF
#     row of 4 MWh carrying another National Grid ID, 'U5-OTHER', and an R1
#     row (U5) two hours after the cutoff: at the cutoff the first and latest
#     held rows disagree on the unit, a conflict, as the mapped view would
#     show then;
#   U5 at C's period 40 (6 MWh): its II row carries an old National Grid ID,
#     'U5-OLD', before its SF row (U5): the first held row conflicts;
#   U5 at C's period 42 (5 MWh): its SF row (U5) and a II row carrying
#     'U5-TIE' are stored at the same time, so the first held row is the II
#     one (ties by run order): a conflict;
#   Y1 at C's period 40 (2 MWh, active on a PN of 2 there): its Elexon ID has
#     two registry units, Y1 and Y1B: ambiguous;
#   U2 at A's 12:15 UTC half-hour (3 MWh), a stale row of the shortfall table
#     still carrying U1's Elexon ID, 'E_U1': U1's held row there is mapped to
#     U1, not U2.
# Read although they look odd: U5 at C's period 41 (5 MWh), whose SF row (U5)
# is stored a day before a II row carrying 'U5-PRE' (the first stored row is
# SF, so it is mapped), and U5 at C's period 43 (5 MWh), whose SF row carries
# 'u5' (letter case is ignored). A row of an unknown run code, 'XX' (999 MWh
# for A's U1 at period 5), is not read. U5's unit-period at A's period 2 (PN
# 0) holds 0 at the cutoff while the table now shows 4 there: it is idle on
# the held value. U1 has a determinable unit-period on 30 August, before the
# window, with no stored row: it counts nowhere. The expected energy is the
# unexplained shortfall plus the metered value, and null where the table
# cannot judge the PN side, so every value above is the one the analysis
# computes.
#
# B1610 rows read, on 1 to 5 September (the London dates of the window's
# first half-hour and of analysis_read_until): one per unit-period of a
# cohort unit there stored by the cutoff, 295 (U1 and U2: 48 + 46 + 45 each,
# 278; U1's null B47 and 12:15 rows, U4, U5 at A's period 2 and C's periods
# 30, 40, 41, 42 and 43, IE1, IF1, P1, F1, IM1, EP1, Z1 and Y1: 17; U5 at C's
# period 20 is stored after the cutoff; OP1 is on 31 August and E on 6
# September), with A's 49 U1 SF rows, B's U1 II row, F1's II row, M1's two
# rows and U5's II rows at C's periods 30, 40, 41 and 42: 352. The 'XX' row
# is not read. The shortfall table's latest capture on settlement dates up to
# 5 September is 25 September 06:00 UTC; E's rows (6 September) were
# captured at 11:00. The registry extract was retrieved on 24 September at
# 05:00 UTC.
Q4_FIXTURES = """
create temporary table synthetic_carbon_error (
    period_start timestamptz,
    error_final numeric,
    final_horizon_seconds numeric
) on commit drop;
create temporary table synthetic_shortfall (
    national_grid_bm_unit text,
    settlement_date date,
    period_start_utc timestamptz,
    unexplained_shortfall_mwh numeric,
    determinability text,
    pn_mwh numeric default 1,
    metered_mwh numeric default 1,
    settlement_period integer,
    elexon_bm_unit text,
    expected_mwh numeric,
    last_captured_at timestamptz default '2026-09-25 06:00:00+00'
) on commit drop;
create temporary table synthetic_cohort (
    national_grid_bm_unit text,
    elexon_bm_unit text,
    fuel_type text,
    fuel_group text
) on commit drop;
create temporary table synthetic_q4_registry (
    national_grid_bm_unit text,
    elexon_bm_unit text,
    retrieved_at timestamptz default '2026-09-24 05:00:00+00'
) on commit drop;
create temporary table synthetic_q4_b1610 (
    bm_unit text,
    national_grid_bm_unit_id text,
    settlement_date date,
    settlement_period integer,
    period_start_utc timestamptz,
    settlement_run_type text,
    quantity numeric,
    retrieved_at timestamptz
) on commit drop;
create temporary table synthetic_run_codes (
    settlement_run_code text,
    run_order integer
) on commit drop;
insert into synthetic_run_codes values
    ('II', 1), ('SF', 2), ('R1', 3), ('R2', 4), ('R3', 5), ('RF', 6), ('DF', 7);
create temporary table synthetic_pn_periods (
    national_grid_bm_unit text,
    settlement_date date,
    period_start_utc timestamptz,
    pn_mwh numeric,
    mapping_status text
) on commit drop;
create temporary table synthetic_metered_periods (
    bm_unit text,
    settlement_date date,
    period_start_utc timestamptz,
    first_quantity_mwh numeric,
    latest_quantity_mwh numeric,
    mapping_status text,
    mapped_national_grid_bm_unit text
) on commit drop;

insert into synthetic_cohort values
    ('U1', 'E_U1', 'CCGT', 'gas'), ('U2', 'E_U2', 'CCGT', 'gas'),
    ('U4', 'E_U4', 'CCGT', 'gas'), ('U5', 'E_U5', 'WIND', 'wind'),
    ('IE1', 'E_IE1', 'INTELE', 'unclassified'), ('IF1', 'E_IF1', 'INTFR', 'interconnector'),
    ('IM1', 'E_IM1', 'PS', 'pumped storage'), ('P1', 'E_P1', 'OCGT', 'gas'),
    ('M1', 'E_M1', 'BIOMASS', 'biomass'), ('F1', 'E_F1', 'NPSHYD', 'hydro'),
    ('BW1', 'E_BW1', 'CCGT', 'gas'), ('AW1', 'E_AW1', 'CCGT', 'gas'),
    ('NR1', 'E_NR1', 'CCGT', 'gas'), ('EP1', 'E_EP1', 'CCGT', 'gas'),
    ('OP1', 'E_OP1', 'CCGT', 'gas'), ('Z1', 'E_Z1', 'CCGT', 'gas'),
    ('Y1', 'E_Y1', 'CCGT', 'gas');
insert into synthetic_q4_registry
select national_grid_bm_unit, elexon_bm_unit from synthetic_cohort;
insert into synthetic_q4_registry values ('Y1B', 'E_Y1');

insert into synthetic_pn_periods values
    ('U1', '2026-09-01', '2026-09-01 01:00+00', 1, 'mapped'),
    ('U2', '2026-09-01', '2026-09-01 01:00+00', 1, 'mapped'),
    ('U4', '2026-09-01', '2026-09-01 01:00+00', 1, 'mapped'),
    ('U5', '2026-09-01', '2026-09-01 01:00+00', 1, 'mapped'),
    ('IE1', '2026-09-01', '2026-09-01 01:30+00', 5, 'mapped'),
    ('IF1', '2026-09-01', '2026-09-01 01:30+00', 5, 'mapped'),
    ('IM1', '2026-09-03', '2026-09-03 03:30+00', 0, 'mapped'),
    ('P1', '2026-09-01', '2026-08-31 23:00+00', 3, 'mapped'),
    ('F1', '2026-09-03', '2026-09-03 03:30+00', 0, 'mapped'),
    ('BW1', '2026-08-31', '2026-08-31 22:30+00', 4, 'mapped'),
    ('AW1', '2026-09-05', '2026-09-05 00:30+00', 4, 'mapped'),
    ('EP1', '2026-09-01', '2026-09-01 01:00+00', 2, 'mapped'),
    ('OP1', '2026-09-01', '2026-09-01 01:00+00', 2, 'mapped'),
    ('Z1', '2026-09-01', '2026-08-31 23:30+00', 0, 'mapped'),
    ('Y1', '2026-09-03', '2026-09-03 18:30+00', 2, 'mapped');

insert into synthetic_metered_periods values
    ('E_M1', '2026-09-05', '2026-09-05 00:00+00', 0, 2, 'mapped', 'M1'),
    ('E_F1', '2026-09-03', '2026-09-03 03:30+00', 3, 0, 'mapped', 'F1'),
    ('E_IM1', '2026-09-03', '2026-09-03 03:30+00', -2, -2, 'mapped', 'IM1'),
    ('E_IM1', '2026-09-03', '2026-09-03 04:00+00', 0, 0, 'mapped', 'IM1'),
    ('E_Z1', '2026-09-01', '2026-08-31 23:30+00', 0, 0, 'mapped', 'Z1');

create temporary table synthetic_periods on commit drop as
select
    days.settlement_date,
    periods.period,
    (days.settlement_date::timestamp at time zone 'Europe/London')
        + (periods.period - 1) * interval '30 minutes' as period_start,
    case
        when days.settlement_date = date '2026-09-01' and periods.period <= 9 then {a_bottom}
        when days.settlement_date = date '2026-09-01' and periods.period = 10 then {a_tenth}
        when days.settlement_date = date '2026-09-02' and periods.period <= 10 then {b_bottom}
        when days.settlement_date = date '2026-09-02' and periods.period >= 47 then null
        when days.settlement_date = date '2026-09-03' and periods.period >= 46 then null
        when days.settlement_date in (date '2026-09-01', date '2026-09-02') and periods.period >= 39 then 1.25
        when days.settlement_date in (date '2026-09-01', date '2026-09-02') then 1
        else 50
    end as cohort_measure
from (
    values (date '2026-09-01'), (date '2026-09-02'), (date '2026-09-03'), (date '2026-09-06'){extra_days}
) as days (settlement_date)
cross join generate_series(1, 48) as periods (period);

insert into synthetic_carbon_error
select
    period_start,
    case
        when settlement_date = date '2026-09-02' and period = 39 then {b39_error}
        when period % 2 = 1 then period
        else -period
    end,
    case
        when settlement_date = date '2026-09-03' and period = 1 then 1801
        when settlement_date = date '2026-08-31' then 1801
        when settlement_date = date '2026-09-02' and period <= {stale_b_half_hours} then 1801
        else 1800
    end
from synthetic_periods;

insert into synthetic_shortfall
select 'U1', settlement_date, period_start, cohort_measure, 'determinable'
from synthetic_periods
where cohort_measure is not null
union all
select 'U2', settlement_date, period_start, -5, 'determinable'
from synthetic_periods
where cohort_measure is not null
union all
select 'U1', settlement_date, period_start, null, 'determinable'
from synthetic_periods
where settlement_date = date '2026-09-02' and period = 47
union all
select 'U3', settlement_date, period_start, 100, 'determinable'
from synthetic_periods
where settlement_date = date '2026-09-01' and period = 1
union all
select 'U4', settlement_date, period_start, 100, 'not determinable'
from synthetic_periods
where settlement_date = date '2026-09-01' and period = 1
union all
select 'U5', settlement_date, period_start, 0, 'determinable'
from synthetic_periods
where settlement_date = date '2026-09-01' and period = 2
union all
select 'U5', settlement_date, period_start, 9, 'determinable'
from synthetic_periods
where settlement_date = date '2026-09-03' and period = 20
union all
select 'U5', settlement_date, period_start, 8, 'determinable'
from synthetic_periods
where settlement_date = date '2026-09-03' and period = 30
union all
select 'U5', settlement_date, period_start, 6, 'determinable'
from synthetic_periods
where settlement_date = date '2026-09-03' and period = 40
union all
select 'U5', settlement_date, period_start, 5, 'determinable'
from synthetic_periods
where settlement_date = date '2026-09-03' and period between 41 and 43
union all
select 'Y1', settlement_date, period_start, 2, 'determinable'
from synthetic_periods
where settlement_date = date '2026-09-03' and period = 40
union all
select 'Z1', settlement_date, period_start, 0, 'determinable'
from synthetic_periods
where settlement_date = date '2026-09-01' and period = 2
union all
select 'U1', date '2026-09-01', timestamptz '2026-09-01 12:15:00+00', 5, 'determinable'
union all
select 'U1', date '2026-08-30', timestamptz '2026-08-30 03:30:00+00', 7, 'determinable'
union all
select 'IE1', settlement_date, period_start, 100, 'determinable'
from synthetic_periods
where settlement_date = date '2026-09-01' and period = 40
union all
select 'IF1', settlement_date, period_start, 100, 'determinable'
from synthetic_periods
where settlement_date = date '2026-09-02' and period = 40
union all
select 'P1', settlement_date, period_start, null, 'pn_incomplete'
from synthetic_periods
where settlement_date = date '2026-09-01' and period = 1
union all
select 'F1', settlement_date, period_start, 0, 'determinable'
from synthetic_periods
where settlement_date = date '2026-09-03' and period = 10
union all
select 'IM1', settlement_date, period_start, 2, 'determinable'
from synthetic_periods
where settlement_date = date '2026-09-03' and period = 10
union all
select 'EP1', date '2026-09-05', timestamptz '2026-09-05 00:00:00+00', null, 'no_metered_value'
union all
select 'OP1', date '2026-08-31', timestamptz '2026-08-31 22:30:00+00', null, 'no_metered_value';

update synthetic_shortfall set pn_mwh = 0, metered_mwh = 0
where national_grid_bm_unit in ('U4', 'U5', 'F1', 'Z1');
update synthetic_shortfall set pn_mwh = 0
where national_grid_bm_unit = 'U2' and period_start_utc = timestamptz '2026-09-01 00:00+00';
update synthetic_shortfall set pn_mwh = 0, metered_mwh = -2
where national_grid_bm_unit = 'IM1';
update synthetic_shortfall
set
    elexon_bm_unit = 'E_' || national_grid_bm_unit,
    settlement_period = 1 + floor(
        extract(epoch from period_start_utc
            - (settlement_date::timestamp at time zone 'Europe/London')) / 1800
    );
update synthetic_shortfall set settlement_period = 50
where period_start_utc = timestamptz '2026-09-01 12:15:00+00';
update synthetic_shortfall
set metered_mwh = settlement_period + 100 * extract(day from settlement_date)
where national_grid_bm_unit = 'U1';
update synthetic_shortfall
set expected_mwh = unexplained_shortfall_mwh + metered_mwh
where determinability = 'determinable';

insert into synthetic_q4_b1610
select
    elexon_bm_unit,
    national_grid_bm_unit,
    settlement_date,
    settlement_period,
    period_start_utc,
    case
        when national_grid_bm_unit = 'U1' and settlement_date = date '2026-09-01'
            then 'R1'
        else 'SF'
    end,
    metered_mwh,
    case
        when national_grid_bm_unit = 'U1' and settlement_date = date '2026-09-01'
            then timestamptz '2026-09-24 08:00:00+00'
        else timestamptz '2026-09-20 10:00:00+00'
    end
from synthetic_shortfall
where settlement_date >= date '2026-08-31';
insert into synthetic_q4_b1610
select elexon_bm_unit, national_grid_bm_unit, settlement_date, settlement_period,
    period_start_utc, 'SF', metered_mwh + 7, timestamptz '2026-09-20 10:00:00+00'
from synthetic_shortfall
where national_grid_bm_unit = 'U1' and settlement_date = date '2026-09-01';
update synthetic_q4_b1610 set retrieved_at = timestamptz '2026-09-25 12:00:00+00'
where bm_unit = 'E_U1' and settlement_date = date '2026-09-02' and settlement_period = 1;
update synthetic_q4_b1610 set retrieved_at = timestamptz '2026-09-25 13:00:00+00'
where bm_unit = 'E_U5' and settlement_date = date '2026-09-03' and settlement_period = 20;
update synthetic_q4_b1610 set retrieved_at = timestamptz '2026-09-24 09:00:00+00'
where bm_unit = 'E_U1' and settlement_date = date '2026-09-01' and settlement_period = 4
    and settlement_run_type = 'SF';
update synthetic_q4_b1610 set national_grid_bm_unit_id = 'U5-OTHER', quantity = 4
where bm_unit = 'E_U5' and settlement_date = date '2026-09-03' and settlement_period = 30;
insert into synthetic_q4_b1610 values
    ('E_U1', 'U1', '2026-09-02', 1, '2026-09-01 23:00+00', 'II', 4, '2026-09-09 10:00:00+00'),
    ('E_U1', 'U1', '2026-09-01', 3, '2026-09-01 00:00+00', 'RF', 1000, '2026-09-25 12:00:01+00'),
    ('E_F1', 'F1', '2026-09-03', 10, '2026-09-03 03:30+00', 'II', 3, '2026-09-09 10:00:00+00'),
    ('E_M1', 'M1', '2026-09-05', 3, '2026-09-05 00:00+00', 'SF', 0, '2026-09-20 10:00:00+00'),
    ('E_M1', 'M1', '2026-09-05', 3, '2026-09-05 00:00+00', 'R1', 2, '2026-09-24 08:00:00+00'),
    ('E_U5', 'U5', '2026-09-03', 30, '2026-09-03 13:30+00', 'II', 0, '2026-09-09 10:00:00+00'),
    ('E_U5', 'U5', '2026-09-03', 30, '2026-09-03 13:30+00', 'R1', 0, '2026-09-25 14:00:00+00'),
    ('E_U5', 'U5-OLD', '2026-09-03', 40, '2026-09-03 18:30+00', 'II', 0, '2026-09-09 10:00:00+00'),
    ('E_U5', 'U5-PRE', '2026-09-03', 41, '2026-09-03 19:00+00', 'II', 0, '2026-09-21 10:00:00+00'),
    ('E_U5', 'U5-TIE', '2026-09-03', 42, '2026-09-03 19:30+00', 'II', 0, '2026-09-20 10:00:00+00'),
    ('E_U1', 'U1', '2026-09-01', 5, '2026-09-01 01:00+00', 'XX', 999, '2026-09-20 10:00:00+00');
update synthetic_q4_b1610 set national_grid_bm_unit_id = 'u5'
where bm_unit = 'E_U5' and settlement_date = date '2026-09-03' and settlement_period = 43;
update synthetic_shortfall set metered_mwh = 4, unexplained_shortfall_mwh = -4
where national_grid_bm_unit = 'U5' and settlement_date = date '2026-09-01'
    and settlement_period = 2;
update synthetic_shortfall set last_captured_at = '2026-09-25 11:00:00+00'
where settlement_date = date '2026-09-06';
insert into synthetic_shortfall (
    national_grid_bm_unit, settlement_date, period_start_utc, unexplained_shortfall_mwh,
    determinability, pn_mwh, metered_mwh, settlement_period, elexon_bm_unit, expected_mwh
) values ('U2', '2026-09-01', '2026-09-01 12:15:00+00', 3, 'determinable', 1, 1, 50, 'E_U1', 4);
{settlement_runs}
create temporary table synthetic_slot_polls (
    poll_slot timestamptz,
    is_first_poll_in_slot boolean
) on commit drop;
insert into synthetic_slot_polls values
    ('2026-09-05 00:30+00', true), ('2026-09-05 01:00+00', true), ('2026-09-05 01:30+00', true);
"""

Q10_TABLES = {
    "int_carbon_error_by_period": "pg_temp.synthetic_carbon_error",
    "int_carbon_outturn__latest": "pg_temp.synthetic_q10_outturn",
    "int_carbon_forecast__revisions": "pg_temp.synthetic_slot_polls",
}
Q4_TABLES = {
    "int_carbon_error_by_period": "pg_temp.synthetic_carbon_error",
    "int_shortfall_by_unit_period": "pg_temp.synthetic_shortfall",
    "int_elexon__bm_unit_cohort": "pg_temp.synthetic_cohort",
    "int_elexon__pn_period_mapped": "pg_temp.synthetic_pn_periods",
    "int_elexon__b1610_period_mapped": "pg_temp.synthetic_metered_periods",
    "int_carbon_forecast__revisions": "pg_temp.synthetic_slot_polls",
    "stg_elexon__b1610": "pg_temp.synthetic_q4_b1610",
    "elexon_settlement_run_codes": "pg_temp.synthetic_run_codes",
    "int_elexon__bm_units_current": "pg_temp.synthetic_q4_registry",
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


POPULATION = (1, "population: matched days (2 needed)", None)
LEFT_OUT = (
    3,
    "half-hours with error_final left out: final forecast over 30 minutes ahead",
    None,
)
NO_FRESH_DAYS = (
    3,
    "matched days with no half-hour whose final forecast is within 30 minutes",
    None,
)
BOTTOM = (1, "bottom quintile mean, MWh", None)
TOP = (1, "top quintile mean, MWh", None)
DIFFERENCE = (1, "difference, top minus bottom, MWh", None)
RATIO = (1, "ratio, top over bottom", None)
VERDICT = (1, "verdict (supported at a ratio of 1.25 or more)", None)
DRAWS = (1, "bootstrap draws with a ratio (900 of 1000 needed)", None)
LOW = (1, "bootstrap 90% interval, low", None)
HIGH = (1, "bootstrap 90% interval, high", None)
QUINTILE = "quintile of abs(error_final): mean positive shortfall, MWh"
Q4_BOTTOM_TIES = (
    "half-hours sharing the bottom quintile's largest abs(error_final) "
    "(value: that error)"
)
Q4_TOP_TIES = (
    "half-hours sharing the top quintile's smallest abs(error_final) "
    "(value: that error)"
)
# Section 1 leads with the ratio and its interval, then the verdict.
Q4_SECTION_ONE_ORDER = (
    RATIO,
    LOW,
    HIGH,
    DRAWS,
    VERDICT,
    BOTTOM,
    TOP,
    DIFFERENCE,
    POPULATION,
)
WEEKDAY_NAMES = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)


def stale_finals_key(weekday_name, kept):
    if kept:
        return (
            4,
            "matched-day half-hours kept, final forecast at most 30 minutes ahead: "
            f"settlement date on a {weekday_name}",
            None,
        )
    return (
        4,
        "matched-day half-hours left out, final forecast over 30 minutes ahead: "
        f"settlement date on a {weekday_name}",
        None,
    )


def stale_finals_expected(kept_by_weekday, left_out_by_weekday):
    """Every weekday's kept and left-out rows; weekdays not named hold 0."""

    expected = {}
    for weekday_name in WEEKDAY_NAMES:
        expected[stale_finals_key(weekday_name, kept=True)] = dict(
            half_hours=kept_by_weekday.get(weekday_name, 0)
        )
        expected[stale_finals_key(weekday_name, kept=False)] = dict(
            half_hours=left_out_by_weekday.get(weekday_name, 0)
        )
    return expected


ACTIVE_UNITS = (5, "cohort units: active in the window (value: units)", None)
INTERCONNECTOR_UNITS = (
    5,
    "cohort units: interconnectors, fuel type beginning INT, left out (value: units)",
    None,
)
INTELE_UNITS = (
    5,
    "cohort units: interconnectors with fuel type INTELE, an interconnector by "
    "assumption (value: units)",
    None,
)
INACTIVE_UNITS = (
    5,
    "cohort units: inactive in the window, left out (value: units)",
    None,
)
NO_ROW_UNITS = (
    5,
    "cohort units: inactive with no row in the window (value: units)",
    None,
)
NO_PN_UNITS = (
    5,
    "active units with no PN unit-period in the window, adding nothing to the sums "
    "(value: units)",
    None,
)
IDLE_UNIT_PERIODS = (
    5,
    "idle unit-periods behind the population half-hours: determinable, PN 0 and "
    "metered 0 (value: unit-periods)",
    None,
)
DETERMINABLE_UNIT_PERIODS = (
    5,
    "determinable unit-periods of active units behind the population half-hours "
    "(value: unit-periods)",
    None,
)
IDLE_SHARE = (5, "idle share of those determinable unit-periods", None)
Q4_COHORT_EXPECTED = {
    ACTIVE_UNITS: dict(value="10"),
    INTERCONNECTOR_UNITS: dict(value="2"),
    INTELE_UNITS: dict(value="1"),
    INACTIVE_UNITS: dict(value="5"),
    NO_ROW_UNITS: dict(value="3"),
    NO_PN_UNITS: dict(value="2"),
    IDLE_UNIT_PERIODS: dict(value="1"),
    DETERMINABLE_UNIT_PERIODS: dict(value="189"),
    IDLE_SHARE: dict(value="0.005291"),
}

CAPTURE_CUTOFF_ROW = (
    3,
    "B1610 rows read: stored at or before shortfall_error_capture_cutoff "
    "(result: that cutoff)",
    None,
)
ELIGIBLE_DATES = (
    3,
    "settlement dates with both values in any half-hour: eligible, every "
    "determinable active cohort unit-period on the three-week run (SF) or later",
    None,
)
ELIGIBLE_MATCHED_DAYS = (3, "matched days on eligible dates (2 needed)", None)
LEFT_OUT_ON_II = (
    3,
    "settlement dates left out: a determinable active cohort unit-period on the "
    "one-week run (II) (value: those unit-periods)",
    None,
)
HELD_BACK_WITHOUT_SF = (
    3,
    "settlement dates left out: of those, held back by unit-periods with no SF or "
    "later row while the date holds one (value: those unit-periods)",
    None,
)
NO_ROW_BY_CUTOFF = (
    3,
    "active cohort unit-periods with an expected energy but no B1610 row mapped to "
    "the unit stored by the cutoff, left out (value: unit-periods)",
    None,
)
ROWS_READ = (
    3,
    "B1610 rows read: stored by the cutoff, cohort Elexon IDs, the window's "
    "settlement dates (value: rows)",
    None,
)
TABLE_CAPTURE = (
    3,
    "shortfall table: its latest capture, max(last_captured_at), on settlement "
    "dates up to analysis_read_until's, beside the cutoff (result: UTC)",
    None,
)
REGISTRY_EXTRACT = (
    3,
    "registry extract read for the cohort and the B1610 mapping, not limited by "
    "the cutoff (result: its retrieved_at, UTC)",
    None,
)
TABLE_CAPTURED_AT = "2026-09-25 06:00:00+00"
WHOLLY_ON_II = "wholly on II"
PARTLY_ON_II = "partly on II: the date holds SF or a later run"
NOT_THE_READ_UNTIL_DATE = "not the settlement date of analysis_read_until, 2026-09-05"


def left_out_date_key(settlement_date):
    return (
        3,
        f"left out, on the one-week run (II): {settlement_date} "
        "(value: unit-periods on II)",
        None,
    )


def gate_day_key(days_needed):
    return (
        3,
        f"eligible matched day number {days_needed}: the date the gate is reached "
        "(first_date)",
        None,
    )


# Every date on SF or later at the cutoff: A partly on R1, B with its period-1
# SF row stored exactly at the cutoff, and C. Six unit-periods are left out
# for no held row mapped to the unit (U5 at C's periods 20, 30, 40 and 42,
# Y1 at 40, and the stale U2 row), not U1's before the window. B, the second eligible matched day, reaches
# the gate.
Q4_ALL_ELIGIBLE_EXPECTED = {
    ROWS_READ: dict(value="352"),
    REGISTRY_EXTRACT: dict(result="2026-09-24 05:00:00+00"),
    TABLE_CAPTURE: dict(result=TABLE_CAPTURED_AT),
    gate_day_key(2): dict(first_date="2026-09-02", result=NOT_THE_READ_UNTIL_DATE),
    CAPTURE_CUTOFF_ROW: dict(result=Q4_CAPTURE_CUTOFF),
    ELIGIBLE_DATES: dict(days=3, first_date="2026-09-01", last_date="2026-09-03"),
    ELIGIBLE_MATCHED_DAYS: dict(days=2, result="met"),
    LEFT_OUT_ON_II: dict(
        half_hours=0, days=0, first_date=None, last_date=None, value="0"
    ),
    HELD_BACK_WITHOUT_SF: dict(
        half_hours=0, days=0, first_date=None, last_date=None, value="0"
    ),
    NO_ROW_BY_CUTOFF: dict(value="6"),
}

# Q4 with A's and B's first ten half-hours at 1 except A10 at 3; 11 to 38 at
# 1; 39 onwards at 1.25. A10 and B10 share an error of 10 at the edge of
# quintile 1, and the tie order (md5 of the seed, draw 0, pick 0 and the
# half-hour) puts B10 in it, where a date order would put A10. Quintile 1: 19
# at 1. Quintile 2 (positions 20 to 38): A10 at 3 and 18 at 1, 21 / 19.
# Quintile 5: 18 at 1.25. Ratio 1.25 exactly: supported at the boundary.
#
# Bootstrap: a draw picks two days. A twice (96 rows: 20, 19, 19, 19, 19):
# quintile 1 is A1 to A10 twice, 24 / 20 = 1.2; quintile 5 is 19 of A39 to
# A48 twice (1.25); ratio 1.041667. A and B: 1.25 when B10 falls in quintile
# 1, as in the observed sample. B twice: quintile 1 is all 1s and quintile 5
# all 1.25: 1.25. A-twice draws are about a quarter, so the 5th percentile is
# 1.041667 and the 95th 1.25. (An independent model of the tie order and the
# picks gives the same values.)
#
# A39 and B39 share the top quintile's smallest error, 39, and both are in
# it. Every matched-day half-hour is fresh: 48 on Tuesday 1 September and 46
# on Wednesday 2 September (the first two of each date start on the UTC day
# before).
Q4_EXPECTED = {
    POPULATION: dict(
        position=9,
        half_hours=94,
        days=2,
        first_date="2026-09-01",
        last_date="2026-09-02",
        result="reached",
    ),
    BOTTOM: dict(position=6, value="1.000000"),
    TOP: dict(position=7, value="1.250000"),
    DIFFERENCE: dict(position=8, value="0.250000"),
    RATIO: dict(position=1, value="1.250000"),
    VERDICT: dict(position=5, result="supported"),
    DRAWS: dict(position=4, value="1000", result="available"),
    LOW: dict(position=2, value="1.041667"),
    HIGH: dict(position=3, value="1.250000"),
    (2, Q4_TOP_TIES, None): dict(
        half_hours=2, value="39", result="in the top quintile: 2"
    ),
    **stale_finals_expected({"Tuesday": 48, "Wednesday": 46}, {}),
    **Q4_COHORT_EXPECTED,
    (2, QUINTILE, 1): dict(
        half_hours=19, value="1.000000", result="abs(error_final) 1 to 10"
    ),
    (2, QUINTILE, 2): dict(
        half_hours=19, value="1.105263", result="abs(error_final) 10 to 19"
    ),
    (2, QUINTILE, 3): dict(
        half_hours=19, value="1.000000", result="abs(error_final) 20 to 29"
    ),
    (2, QUINTILE, 4): dict(
        half_hours=19, value="1.000000", result="abs(error_final) 29 to 38"
    ),
    (2, QUINTILE, 5): dict(
        half_hours=18, value="1.250000", result="abs(error_final) 39 to 48"
    ),
    (2, Q4_BOTTOM_TIES, None): dict(
        half_hours=2, value="10", result="in the bottom quintile: 1"
    ),
    (3, "settlement dates with an error_final", None): dict(
        days=3, first_date="2026-09-01", last_date="2026-09-03"
    ),
    LEFT_OUT: dict(half_hours=1),
    NO_FRESH_DAYS: dict(days=0),
    (3, "settlement dates with the cohort measure", None): dict(
        days=3, first_date="2026-09-01", last_date="2026-09-03"
    ),
    (3, "settlement dates with both values in any half-hour", None): dict(days=3),
    (3, "on matched days: cohort measure without error_final", None): dict(
        half_hours=1, days=1
    ),
    (3, "on matched days: error_final without the cohort measure", None): dict(
        half_hours=2, days=1
    ),
    (3, "cohort unit-periods per population half-hour: min", None): dict(
        half_hours=94, value="2"
    ),
    (3, "cohort unit-periods per population half-hour: median", None): dict(value="2"),
    (3, "cohort unit-periods per population half-hour: max", None): dict(value="3"),
    **Q4_ALL_ELIGIBLE_EXPECTED,
}

# B's U2 at period 5 holds only II, and all of C is on II: C's SF rows become
# II rows, and where a II row is already stored (F1 at period 10, U5 at
# periods 30, 40, 41 and 42) the SF row is not stored at all. B leaves (46
# half-hours with both values; one unit-period on II, while the rest of B is
# on SF: held back by a unit-period with no SF row), and so does C (45
# half-hours; U1 and U2 in each, F1 at period 10, U5 at period 30, now mapped
# on its II row alone, and U5 at period 43: 93 unit-periods on II). Six are
# left out for no held row mapped to the unit: U5 at C's period 20 (stored
# after the cutoff), U5 at 40, 41 and 42 (each now holds only a II row with
# another National Grid ID), Y1 at 40 and the stale U2 row. Only A is a
# matched day, one of the two needed, although B has 46 half-hours with both
# values, so the gate is not reached. A's 48 population half-hours hold U1
# and U2, and U5 at period 2.
Q4_ON_II_RUNS = """
update synthetic_q4_b1610 set settlement_run_type = 'II'
where bm_unit = 'E_U2' and settlement_date = date '2026-09-02' and settlement_period = 5;
delete from synthetic_q4_b1610 as sf
where sf.settlement_date = date '2026-09-03'
    and sf.settlement_run_type = 'SF'
    and exists (
        select 1
        from synthetic_q4_b1610 as ii
        where ii.bm_unit = sf.bm_unit
            and ii.settlement_date = sf.settlement_date
            and ii.settlement_period = sf.settlement_period
            and ii.settlement_run_type = 'II'
    );
update synthetic_q4_b1610 set settlement_run_type = 'II'
where settlement_date = date '2026-09-03' and settlement_run_type = 'SF';
"""
Q4_ON_II_EXPECTED = {
    POPULATION: dict(
        half_hours=48,
        days=1,
        first_date="2026-09-01",
        last_date="2026-09-01",
        result="not reached",
    ),
    VERDICT: dict(result="population not reached: no result"),
    ELIGIBLE_DATES: dict(days=1, first_date="2026-09-01", last_date="2026-09-01"),
    ELIGIBLE_MATCHED_DAYS: dict(days=1, result="not met"),
    LEFT_OUT_ON_II: dict(
        half_hours=91,
        days=2,
        first_date="2026-09-02",
        last_date="2026-09-03",
        value="94",
    ),
    HELD_BACK_WITHOUT_SF: dict(
        half_hours=46,
        days=1,
        first_date="2026-09-02",
        last_date="2026-09-02",
        value="1",
    ),
    left_out_date_key("2026-09-02"): dict(
        half_hours=46,
        days=1,
        first_date="2026-09-02",
        last_date="2026-09-02",
        value="1",
        result=PARTLY_ON_II,
    ),
    left_out_date_key("2026-09-03"): dict(
        half_hours=45,
        days=1,
        first_date="2026-09-03",
        last_date="2026-09-03",
        value="93",
        result=WHOLLY_ON_II,
    ),
    NO_ROW_BY_CUTOFF: dict(value="6"),
    gate_day_key(2): dict(first_date=None, result="not reached"),
    NO_FRESH_DAYS: dict(days=0),
    (3, "on matched days: error_final without the cohort measure", None): dict(
        half_hours=0, days=0
    ),
    (3, "cohort unit-periods per population half-hour: min", None): dict(
        half_hours=48, value="2"
    ),
    (3, "cohort unit-periods per population half-hour: max", None): dict(value="3"),
    **stale_finals_expected({"Tuesday": 48}, {}),
}

# Compiled without analysis_read_until: E (6 September, 48 half-hours on SF)
# joins A and B, three matched days of the two needed, and still no result.
Q4_WITHOUT_READ_UNTIL_EXPECTED = {
    POPULATION: dict(days=3, result="not reached"),
    VERDICT: dict(result="population not reached: no result"),
    ELIGIBLE_MATCHED_DAYS: dict(days=3, result="met"),
    gate_day_key(2): dict(
        first_date="2026-09-02", result="no analysis_read_until to compare"
    ),
}

# Compiled without the capture cutoff: every row stored up to the query's own
# time counts (U5's row at C too), two matched days of the two needed, and no
# result.
Q4_WITHOUT_CAPTURE_CUTOFF_EXPECTED = {
    CAPTURE_CUTOFF_ROW: dict(
        result="not recorded: rows stored up to the query's own time; no result"
    ),
    POPULATION: dict(days=2, result="not reached"),
    VERDICT: dict(result="population not reached: no result"),
    ELIGIBLE_MATCHED_DAYS: dict(days=2, result="met"),
    NO_ROW_BY_CUTOFF: dict(value="4"),
    gate_day_key(2): dict(first_date="2026-09-02", result=NOT_THE_READ_UNTIL_DATE),
}

# Reruns after B1610 stored after the cutoff has reached the current tables
# (the shortfall table rebuilt at 14:00 UTC that day). Each leaves every
# worked value of the main case as it is; only the table's latest capture
# moves.
#
# A later quantity: the RF row of A's U1 at period 3 (1,000 MWh, stored a
# second after the cutoff). The table holds metered 1,000 and an unexplained
# shortfall of 104 - 1,000 = -896 there; the analysis still reads R1's 103
# held at the cutoff (104 - 103 = 1).
Q4_RERUN_QUANTITY = """
update synthetic_shortfall
set
    metered_mwh = 1000,
    unexplained_shortfall_mwh = expected_mwh - 1000,
    last_captured_at = '2026-09-25 14:00:00+00'
where national_grid_bm_unit = 'U1'
    and settlement_date = date '2026-09-01'
    and settlement_period = 3;
"""
# A later capture that makes a unit active: Z1 (PN 0, SF 0 at the cutoff)
# gets an R1 row of 5 MWh at 13:00 UTC; the current mapped view and the table
# show 5. At the cutoff Z1 is still inactive, so it adds no fourth
# unit-period to A's period 2 and the active and inactive counts stay 9 and 5.
Q4_RERUN_ACTIVITY = """
insert into synthetic_q4_b1610 values
    ('E_Z1', 'Z1', '2026-09-01', 2, '2026-08-31 23:30+00', 'R1', 5, '2026-09-25 13:00:00+00');
update synthetic_metered_periods set latest_quantity_mwh = 5 where bm_unit = 'E_Z1';
update synthetic_shortfall
set
    metered_mwh = 5,
    unexplained_shortfall_mwh = -5,
    last_captured_at = '2026-09-25 14:00:00+00'
where national_grid_bm_unit = 'Z1';
"""
# A later capture that changes the mapping: an R1 row for U2 at A's period 6
# carrying another National Grid ID, 'U2-NEW', at 13:00 UTC. The table now
# finds a conflict there and holds no metered value (no_metered_value). At
# the cutoff U2's rows agree, so U2 still counts there and every half-hour
# keeps at least two unit-periods.
Q4_RERUN_MAPPING = """
insert into synthetic_q4_b1610 values
    ('E_U2', 'U2-NEW', '2026-09-01', 6, '2026-09-01 01:30+00', 'R1', 1, '2026-09-25 13:00:00+00');
update synthetic_shortfall
set
    determinability = 'no_metered_value',
    metered_mwh = null,
    unexplained_shortfall_mwh = null,
    last_captured_at = '2026-09-25 14:00:00+00'
where national_grid_bm_unit = 'U2'
    and settlement_date = date '2026-09-01'
    and settlement_period = 6;
"""
Q4_RERUNS = (
    ("a later quantity", Q4_RERUN_QUANTITY),
    ("a later capture that makes a unit active", Q4_RERUN_ACTIVITY),
    ("a later capture that changes the mapping", Q4_RERUN_MAPPING),
)


def rerun_expected():
    """The main case's worked values, with the table's later capture."""

    expected = dict(Q4_EXPECTED)
    expected[TABLE_CAPTURE] = dict(result="2026-09-25 14:00:00+00")
    return expected


# B's first ten half-hours at 0, A's at 1: the tie at 10 puts B10 in
# quintile 1, so its bottom mean is A1 to A9 over 19, 9 / 19, and the ratio
# 1.25 x 19 / 9 = 2.638889. Every B-twice draw has a bottom mean of 0 and
# forms no ratio, so fewer than 900 draws are usable.
Q4_UNAVAILABLE_EXPECTED = {
    BOTTOM: dict(value="0.473684"),
    RATIO: dict(value="2.638889"),
    VERDICT: dict(result="supported"),
    LOW: dict(value=None),
    HIGH: dict(value=None),
}

# The same rows with the usable-draw minimum set to exactly the usable count:
# the interval is available at its boundary. Its draws are A twice (1.25)
# and A with B (2.375 when A10 falls in quintile 1, 2.638889 when B10 does),
# the lowest and highest each well over 5% of them.
Q4_BOUNDARY_EXPECTED = {
    LOW: dict(value="1.250000"),
    HIGH: dict(value="2.638889"),
}

# Every bottom half-hour at 0: not applicable, no ratio, no interval.
Q4_NOT_APPLICABLE_EXPECTED = {
    BOTTOM: dict(value="0.000000"),
    TOP: dict(value="1.250000"),
    DIFFERENCE: dict(value="1.250000"),
    RATIO: dict(value=None),
    VERDICT: dict(result="not applicable: bottom quintile mean is 0 or less"),
    DRAWS: dict(value=None, result="not applicable"),
    LOW: dict(value=None),
    HIGH: dict(value=None),
}

Q4_NOT_REACHED_EXPECTED = {
    (1, "population: matched days (3 needed)", None): dict(
        half_hours=94, days=2, result="not reached"
    ),
    VERDICT: dict(result="population not reached: no result"),
    DRAWS: dict(value=None, result=None),
    (2, QUINTILE, 1): dict(half_hours=19),
    (2, Q4_TOP_TIES, None): dict(half_hours=None, value=None, result=None),
    **stale_finals_expected({"Tuesday": 48, "Wednesday": 46}, {}),
    ACTIVE_UNITS: dict(value="10"),
    NO_PN_UNITS: dict(value="2"),
    IDLE_UNIT_PERIODS: dict(value="1"),
    DETERMINABLE_UNIT_PERIODS: dict(value="189"),
    IDLE_SHARE: dict(value="0.005291"),
}

# B39's error at 38: A38, B38 and B39 share the top quintile's smallest
# error, 38, at positions 75 to 77 of 94, and only the 77th is in it.
Q4_TOP_TIE_SPLIT_EXPECTED = {
    (2, Q4_TOP_TIES, None): dict(
        half_hours=3, value="38", result="in the top quintile: 1"
    ),
}

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
Q4_GATED = ({1, 2}, ("value",))
Q4_QUINTILES_GATED = ({2}, ("result",))
Q2RF_GATED = (
    {1},
    (
        "changed_unit_periods",
        "gross_change_mwh",
        "r3_absolute_mwh",
        "gross_share",
        "net_change_mwh",
        "net_share",
    ),
)


# Q2-RF's B1610 rows, with the time each was stored (R3: the back-fill, 24
# August 2026 10:00). The REACHED cutoff is 29 November 2026 12:00, and the
# RF capture (offset 449 there) first asks for a date on its settlement date
# plus 450 days. 22 and 23 August 2025 are the first two dates holding both
# runs; 22 August's RF was stored exactly at the cutoff, two weeks late, and
# 23 August's in two parts (2__SUPP's after a retry). 20 August has only R3,
# its RF capture due and not held; 21 August only RF: its R3 row was stored after
# both cutoffs. 24 August is a third date (outside the first two). T_ZERO's
# R3 is 0, so its group's share is not defined; T_SOLAR has only an R3 row,
# so its group has no unit-period in both runs. extra_cohort_rows lets one
# case give T_GAS-1 a second fuel group; changes runs after the inserts.
Q2RF_FIXTURES = """
create temporary table synthetic_b1610 (
    bm_unit text,
    settlement_date date,
    settlement_period integer,
    settlement_run_type text,
    quantity numeric,
    retrieved_at timestamptz
) on commit drop;
create temporary table synthetic_cohort (
    national_grid_bm_unit text,
    elexon_bm_unit text,
    fuel_group text
) on commit drop;
create temporary table synthetic_registry_extract (
    retrieved_at timestamptz
) on commit drop;

insert into synthetic_cohort values
    ('GAS-1', 'T_GAS-1', 'gas'),
    ('GAS-1A', 'T_GAS-1', 'gas'),   -- the same Elexon ID twice, same group
    ('WIND-1', 'T_WIND-1', 'wind'),
    ('ZERO-1', 'T_ZERO', 'hydro'),
    ('SOLAR-1', 'T_SOLAR', 'solar'),
    ('NOID-1', null, 'gas'){extra_cohort_rows};
insert into synthetic_registry_extract values ('2026-11-29 06:00+00');
{b1610_rows}
{changes}
"""
Q2RF_B1610_ROWS = """
insert into synthetic_b1610 values
    ('T_GAS-1', '2025-08-20', 1, 'R3', 70, '2026-08-24 10:00+00'),
    ('T_GAS-1', '2025-08-21', 1, 'RF', 50, '2026-11-14 00:05+00'),
    ('T_GAS-1', '2025-08-21', 1, 'R3', 1000, '2026-12-01 00:00+00'),
    ('T_GAS-1', '2025-08-22', 1, 'R3', 10, '2026-08-24 10:00+00'),
    ('T_GAS-1', '2025-08-22', 1, 'RF', 12, '2026-11-29 12:00+00'),
    ('T_GAS-1', '2025-08-22', 1, 'SF', 999, '2026-08-24 10:00+00'),
    ('T_WIND-1', '2025-08-22', 1, 'R3', -4, '2026-08-24 10:00+00'),
    ('T_WIND-1', '2025-08-22', 1, 'RF', -1, '2026-11-29 12:00+00'),
    ('2__SUPP', '2025-08-22', 1, 'R3', 5, '2026-08-24 10:00+00'),
    ('2__SUPP', '2025-08-22', 1, 'RF', 5, '2026-11-29 12:00+00'),
    ('T_ZERO', '2025-08-22', 1, 'R3', 0, '2026-08-24 10:00+00'),
    ('T_ZERO', '2025-08-22', 1, 'RF', 3, '2026-11-29 12:00+00'),
    ('T_SOLAR', '2025-08-22', 1, 'R3', 9, '2026-08-24 10:00+00'),
    ('T_GAS-1', '2025-08-22', 2, 'R3', 20, '2026-08-24 10:00+00'),
    ('T_GAS-1', '2025-08-22', 2, 'RF', 15, '2026-11-29 12:00+00'),
    ('T_WIND-1', '2025-08-22', 2, 'R3', 6, '2026-08-24 10:00+00'),
    ('2__SUPP', '2025-08-22', 2, 'RF', 7, '2026-11-29 12:00+00'),
    ('T_GAS-1', '2025-08-23', 1, 'R3', 8, '2026-08-24 10:00+00'),
    ('T_GAS-1', '2025-08-23', 1, 'RF', 8, '2026-11-16 00:05+00'),
    ('2__SUPP', '2025-08-23', 1, 'R3', -10, '2026-08-24 10:00+00'),
    ('2__SUPP', '2025-08-23', 1, 'RF', -6, '2026-11-18 06:00+00'),
    ('T_GAS-1', '2025-08-24', 1, 'R3', 100, '2026-08-24 10:00+00'),
    ('T_GAS-1', '2025-08-24', 1, 'RF', 0, '2026-11-17 00:05+00');
"""
# K2: 22 August's RF and 2__SUPP's 23 August RF stored a microsecond after
# the REACHED cutoff, so neither is held at it.
STORED_JUST_AFTER_THE_CUTOFF = """
update synthetic_b1610
set retrieved_at = '2026-11-29 12:00:00.000001+00'
where settlement_run_type = 'RF'
    and (
        settlement_date = '2025-08-22'
        or (settlement_date = '2025-08-23' and bm_unit = '2__SUPP')
    );
"""

Q2RF_TABLES = {
    "elexon_b1610": "pg_temp.synthetic_b1610",
    "int_elexon__bm_unit_cohort": "pg_temp.synthetic_cohort",
    "int_elexon__bm_units_current": "pg_temp.synthetic_registry_extract",
}
# Sections 2 to 5 never carry energy, before or after the population.
_, Q2RF_ENERGY_COLUMNS = Q2RF_GATED
Q2RF_NO_ENERGY = ({2, 3, 4, 5}, Q2RF_ENERGY_COLUMNS)

# (section, item, None) -> {column: expected}. Gas: 10 to 12, 20 to 15, 8 to
# 8: gross 7 of 38, net -3. Wind: -4 to -1 (6 only in R3): 3 of 4, net 3.
# Outside: 5 to 5, -10 to -6 (7 only in RF): 4 of 15, net 4. Hydro: 0 to 3,
# not defined. Overall: 17 of 57, net 7. The cohort total is overall less
# outside: 5 unit-periods, 13 of 42, net 3.
Q2RF_MEASURES = {
    (1, "cohort total", None): dict(
        position=1,
        unit_periods=5,
        units=3,
        changed_unit_periods=4,
        gross_change_mwh="13.000",
        r3_absolute_mwh="42.000",
        gross_share="0.309524",
        net_change_mwh="3.000",
        net_share="0.071429",
        share_status="defined",
        only_in_r3=2,
        only_in_rf=0,
    ),
    (1, "gas", None): dict(
        position=4,
        unit_periods=3,
        units=1,
        changed_unit_periods=2,
        gross_change_mwh="7.000",
        r3_absolute_mwh="38.000",
        gross_share="0.184211",
        net_change_mwh="-3.000",
        net_share="-0.078947",
        share_status="defined",
        only_in_r3=0,
        only_in_rf=0,
    ),
    (1, "wind", None): dict(
        position=4,
        unit_periods=1,
        gross_change_mwh="3.000",
        r3_absolute_mwh="4.000",
        gross_share="0.750000",
        net_share="0.750000",
        only_in_r3=1,
        only_in_rf=0,
    ),
    (1, "outside the cohort", None): dict(
        position=3,
        unit_periods=2,
        changed_unit_periods=1,
        gross_change_mwh="4.000",
        r3_absolute_mwh="15.000",
        gross_share="0.266667",
        net_change_mwh="4.000",
        only_in_r3=0,
        only_in_rf=1,
    ),
    (1, "hydro", None): dict(
        position=4,
        unit_periods=1,
        gross_change_mwh="3.000",
        r3_absolute_mwh="0.000",
        gross_share=None,
        net_share=None,
        share_status="not defined: sum of abs(R3) is 0",
    ),
    (1, "overall", None): dict(
        position=2,
        unit_periods=7,
        units=4,
        changed_unit_periods=5,
        gross_change_mwh="17.000",
        r3_absolute_mwh="57.000",
        gross_share="0.298246",
        net_change_mwh="7.000",
        net_share="0.122807",
        share_status="defined",
        only_in_r3=2,
        only_in_rf=1,
    ),
    (1, "solar", None): dict(
        position=4,
        unit_periods=0,
        units=0,
        changed_unit_periods=0,
        gross_change_mwh="0.000",
        r3_absolute_mwh="0.000",
        gross_share=None,
        net_change_mwh="0.000",
        share_status="not defined: sum of abs(R3) is 0",
        only_in_r3=1,
        only_in_rf=0,
    ),
}
Q2RF_POPULATION = {
    (1, "gas", None): dict(dates=2, first_date="2025-08-22", last_date="2025-08-23"),
}
# Each date's RF is first asked for at 00:00 UTC, its settlement date plus
# 449 + 1 days: 20 August 2025 on 13 November 2026, and so on. Every August
# date has 48 periods. The dates holding R3 have RF stored, or expected, at:
# 23 August 16 November 00:05, 24 August 17 November 00:05, and 20 and 22
# August at the cutoff (20 August's is due and not held). The second of
# these is 17 November 00:05: the population of two was reached then, with 23
# and 24 August, and 22 August, stored late but before the cutoff, took its
# place by date.
Q2RF_SETTLEMENT_DATES = {
    (4, "date 2025-08-20", None): dict(
        settlement_date="2025-08-20",
        date_status="RF capture due, not held",
        expected_periods=48,
        r3_periods=1,
        rf_periods=0,
        rf_capture_due_at="2026-11-13 00:00:00+00:00",
        rf_first_stored_at=None,
    ),
    (4, "date 2025-08-21", None): dict(
        settlement_date="2025-08-21",
        date_status="RF held, no R3",
        expected_periods=48,
        r3_periods=0,
        rf_periods=1,
        rf_capture_due_at="2026-11-14 00:00:00+00:00",
        rf_first_stored_at="2026-11-14 00:05:00+00:00",
    ),
    (4, "date 2025-08-22", None): dict(
        settlement_date="2025-08-22",
        date_status="in the population",
        expected_periods=48,
        r3_periods=2,
        rf_periods=2,
        rf_capture_due_at="2026-11-15 00:00:00+00:00",
        rf_first_stored_at="2026-11-29 12:00:00+00:00",
    ),
    (4, "date 2025-08-23", None): dict(
        settlement_date="2025-08-23",
        date_status="in the population",
        expected_periods=48,
        r3_periods=1,
        rf_periods=1,
        rf_capture_due_at="2026-11-16 00:00:00+00:00",
        rf_first_stored_at="2026-11-16 00:05:00+00:00",
    ),
    (4, "date 2025-08-24", None): dict(
        settlement_date="2025-08-24",
        date_status="R3 and RF held, after the population",
        expected_periods=48,
        r3_periods=1,
        rf_periods=1,
        rf_capture_due_at="2026-11-17 00:00:00+00:00",
        rf_first_stored_at="2026-11-17 00:05:00+00:00",
    ),
}
Q2RF_EXPECTED = {
    **Q2RF_MEASURES,
    (2, "date 2025-08-22: R3", None): dict(unit_periods=7, units=5, periods=2),
    (2, "date 2025-08-22: RF", None): dict(unit_periods=6, units=4, periods=2),
    (2, "date 2025-08-23: R3", None): dict(unit_periods=2, units=2, periods=1),
    (2, "date 2025-08-23: RF", None): dict(unit_periods=2, units=2, periods=1),
    (
        3,
        "Elexon IDs with more than one cohort fuel group (each would count twice)",
        None,
    ): dict(units=0),
    **Q2RF_SETTLEMENT_DATES,
    (5, "capture cutoff", None): dict(position=1, instant="2026-11-29 12:00:00+00:00"),
    (5, "population reached at", None): dict(
        position=2, instant="2026-11-17 00:05:00+00:00"
    ),
    (5, "registry extract retrieved at", None): dict(
        position=3, instant="2026-11-29 06:00:00+00:00"
    ),
}
# The headline first, then every unit, the units outside the cohort and the
# fuel groups by name.
Q2RF_MEASURES_ORDER = [
    (1, "cohort total", None),
    (1, "overall", None),
    (1, "outside the cohort", None),
    (1, "gas", None),
    (1, "hydro", None),
    (1, "solar", None),
    (1, "wind", None),
]

REPEATED_ID = (
    3,
    "Elexon IDs with more than one cohort fuel group (each would count twice)",
    None,
)
TWO_GROUPS_FOR_ONE_ID = ",\n    ('GAS-1B', 'T_GAS-1', 'wind')"
# With T_GAS-1 in gas and wind, its three unit-periods (10 to 12, 20 to 15,
# 8 to 8) still count once overall: the main run's 7 unit-periods, 17 of 57,
# net 7. Counted once per group they would give 10 unit-periods, 24 of 95,
# net 4. The cohort total likewise stays 5 unit-periods, 13 of 42, net 3;
# adding the fuel-group rows would give 20 of 80.
Q2RF_OVERALL = (1, "overall", None)
Q2RF_TWO_GROUPS_OVERALL = dict(
    unit_periods=7,
    units=4,
    changed_unit_periods=5,
    gross_change_mwh="17.000",
    r3_absolute_mwh="57.000",
    net_change_mwh="7.000",
    only_in_r3=2,
    only_in_rf=1,
)
Q2RF_COHORT_TOTAL = (1, "cohort total", None)
Q2RF_TWO_GROUPS_COHORT_TOTAL = dict(
    unit_periods=5,
    gross_change_mwh="13.000",
    r3_absolute_mwh="42.000",
    net_change_mwh="3.000",
)

# K2: with 22 August's RF stored after the cutoff, the population is 23 and
# 24 August. The cohort total, T_GAS-1 alone: 8 to 8 and 100 to 0, gross 100
# of 108, net -100. 2__SUPP's 23 August R3 row has no RF held at the cutoff.
Q2RF_LATE_EXPECTED = {
    (1, "cohort total", None): dict(
        unit_periods=2,
        gross_change_mwh="100.000",
        r3_absolute_mwh="108.000",
        gross_share="0.925926",
        net_change_mwh="-100.000",
        dates=2,
        first_date="2025-08-23",
        last_date="2025-08-24",
    ),
    (1, "outside the cohort", None): dict(unit_periods=0, only_in_r3=1, only_in_rf=0),
    (4, "date 2025-08-22", None): dict(
        date_status="RF capture due, not held", rf_periods=0, rf_first_stored_at=None
    ),
    (4, "date 2025-08-23", None): dict(
        date_status="in the population",
        rf_first_stored_at="2026-11-16 00:05:00+00:00",
    ),
    (4, "date 2025-08-24", None): dict(date_status="in the population"),
    (5, "population reached at", None): dict(instant="2026-11-17 00:05:00+00:00"),
}

# K4: every settlement date one day earlier, stored times unchanged. The
# population is 21 and 22 August, every measure is as in the main case, each
# date's RF is due a day earlier, and the reach time does not move. Earlier
# rather than later, so the first population date falls before 22 August
# 2025 and a floor typed in at that date fails.
ONE_DAY_EARLIER = "update synthetic_b1610 set settlement_date = settlement_date - 1;"
Q2RF_ONE_DAY_EARLIER_EXPECTED = {
    (1, "gas", None): dict(dates=2, first_date="2025-08-21", last_date="2025-08-22"),
    (2, "date 2025-08-21: R3", None): dict(unit_periods=7, units=5, periods=2),
    (2, "date 2025-08-21: RF", None): dict(unit_periods=6, units=4, periods=2),
    (2, "date 2025-08-22: R3", None): dict(unit_periods=2, units=2, periods=1),
    (2, "date 2025-08-22: RF", None): dict(unit_periods=2, units=2, periods=1),
    REPEATED_ID: dict(units=0),
    (4, "date 2025-08-19", None): dict(
        date_status="RF capture due, not held",
        rf_capture_due_at="2026-11-12 00:00:00+00:00",
    ),
    (4, "date 2025-08-20", None): dict(
        date_status="RF held, no R3",
        rf_capture_due_at="2026-11-13 00:00:00+00:00",
    ),
    (4, "date 2025-08-21", None): dict(
        date_status="in the population",
        rf_capture_due_at="2026-11-14 00:00:00+00:00",
        rf_first_stored_at="2026-11-29 12:00:00+00:00",
    ),
    (4, "date 2025-08-22", None): dict(
        date_status="in the population",
        rf_capture_due_at="2026-11-15 00:00:00+00:00",
    ),
    (4, "date 2025-08-23", None): dict(
        date_status="R3 and RF held, after the population",
        rf_capture_due_at="2026-11-16 00:00:00+00:00",
    ),
    (5, "capture cutoff", None): dict(instant="2026-11-29 12:00:00+00:00"),
    (5, "population reached at", None): dict(instant="2026-11-17 00:05:00+00:00"),
    (5, "registry extract retrieved at", None): dict(
        instant="2026-11-29 06:00:00+00:00"
    ),
}

# K5: one gas unit on the two clock-change days, 27 October 2024 (50 periods)
# and 30 March 2025 (46). Periods 1: 1 to 1 on both; period 50: 4 to 6;
# period 46: 10 to 7. Gross 5 of 16, net -1. RF is first asked for at 00:00
# UTC on 20 January 2026 and 23 June 2026 (01:00 BST: the same time cast in
# a London session would read 22 June 23:00 UTC).
Q2RF_CLOCK_CHANGE_ROWS = """
insert into synthetic_b1610 values
    ('T_GAS-1', '2024-10-27', 1, 'R3', 1, '2026-08-24 10:00+00'),
    ('T_GAS-1', '2024-10-27', 1, 'RF', 1, '2026-01-20 00:05+00'),
    ('T_GAS-1', '2024-10-27', 50, 'R3', 4, '2026-08-24 10:00+00'),
    ('T_GAS-1', '2024-10-27', 50, 'RF', 6, '2026-01-20 00:05+00'),
    ('T_GAS-1', '2025-03-30', 1, 'R3', 1, '2026-08-24 10:00+00'),
    ('T_GAS-1', '2025-03-30', 1, 'RF', 1, '2026-06-23 00:05+00'),
    ('T_GAS-1', '2025-03-30', 46, 'R3', 10, '2026-08-24 10:00+00'),
    ('T_GAS-1', '2025-03-30', 46, 'RF', 7, '2026-06-23 00:05+00');
"""
Q2RF_CLOCK_CHANGE_MEASURES = dict(
    unit_periods=4,
    changed_unit_periods=2,
    gross_change_mwh="5.000",
    r3_absolute_mwh="16.000",
    gross_share="0.312500",
    net_change_mwh="-1.000",
    net_share="-0.062500",
)
Q2RF_CLOCK_CHANGE_EXPECTED = {
    (1, "cohort total", None): Q2RF_CLOCK_CHANGE_MEASURES,
    (1, "overall", None): Q2RF_CLOCK_CHANGE_MEASURES,
    (4, "date 2024-10-27", None): dict(
        expected_periods=50,
        r3_periods=2,
        rf_periods=2,
        rf_capture_due_at="2026-01-20 00:00:00+00:00",
    ),
    (4, "date 2025-03-30", None): dict(
        expected_periods=46,
        r3_periods=2,
        rf_periods=2,
        rf_capture_due_at="2026-06-23 00:00:00+00:00",
    ),
    (5, "population reached at", None): dict(instant="2026-06-23 00:05:00+00:00"),
}

# K6: the production calendar, on the project's 30 dates and 450-day offset,
# at the NOT_REACHED cutoff, 30 November 2026 00:00. One unit, period 1, R3
# for every date from 22 August to 30 September 2025. RF stored five minutes
# after it is due (22 August's due 16 November 2026 00:00) from 22 August to
# 5 September, except 25 August. 5 September's is due exactly at the cutoff
# and stored after it. Held at the cutoff: 13 dates. Expected RF: the 13
# stored times, then 25 August and 5 September at the cutoff, then 6 to 20
# September when due, 1 to 15 December: the 30th is 15 December 00:00.
Q2RF_CALENDAR_ROWS = """
insert into synthetic_b1610
select 'T_GAS-1', date '2025-08-22' + day_number, 1, 'R3', 10, '2026-08-24 10:00+00'
from generate_series(0, 39) as day_number;
insert into synthetic_b1610 values
    ('T_GAS-1', '2025-08-22', 1, 'RF', 11, '2026-11-16 00:05+00'),
    ('T_GAS-1', '2025-08-23', 1, 'RF', 11, '2026-11-17 00:05+00'),
    ('T_GAS-1', '2025-08-24', 1, 'RF', 11, '2026-11-18 00:05+00'),
    ('T_GAS-1', '2025-08-26', 1, 'RF', 11, '2026-11-20 00:05+00'),
    ('T_GAS-1', '2025-08-27', 1, 'RF', 11, '2026-11-21 00:05+00'),
    ('T_GAS-1', '2025-08-28', 1, 'RF', 11, '2026-11-22 00:05+00'),
    ('T_GAS-1', '2025-08-29', 1, 'RF', 11, '2026-11-23 00:05+00'),
    ('T_GAS-1', '2025-08-30', 1, 'RF', 11, '2026-11-24 00:05+00'),
    ('T_GAS-1', '2025-08-31', 1, 'RF', 11, '2026-11-25 00:05+00'),
    ('T_GAS-1', '2025-09-01', 1, 'RF', 11, '2026-11-26 00:05+00'),
    ('T_GAS-1', '2025-09-02', 1, 'RF', 11, '2026-11-27 00:05+00'),
    ('T_GAS-1', '2025-09-03', 1, 'RF', 11, '2026-11-28 00:05+00'),
    ('T_GAS-1', '2025-09-04', 1, 'RF', 11, '2026-11-29 00:05+00'),
    ('T_GAS-1', '2025-09-05', 1, 'RF', 11, '2026-11-30 00:05+00');
"""
IN_THE_POPULATION = dict(date_status="in the population")
RF_NOT_YET_DUE = dict(date_status="RF capture not yet due")
Q2RF_CALENDAR_DATES = {
    (4, "date 2025-08-22", None): dict(
        date_status="in the population",
        rf_capture_due_at="2026-11-16 00:00:00+00:00",
        rf_first_stored_at="2026-11-16 00:05:00+00:00",
    ),
    (4, "date 2025-08-23", None): IN_THE_POPULATION,
    (4, "date 2025-08-24", None): IN_THE_POPULATION,
    (4, "date 2025-08-25", None): dict(
        date_status="RF capture due, not held",
        rf_capture_due_at="2026-11-19 00:00:00+00:00",
        rf_first_stored_at=None,
    ),
    (4, "date 2025-08-26", None): IN_THE_POPULATION,
    (4, "date 2025-08-27", None): IN_THE_POPULATION,
    (4, "date 2025-08-28", None): IN_THE_POPULATION,
    (4, "date 2025-08-29", None): IN_THE_POPULATION,
    (4, "date 2025-08-30", None): IN_THE_POPULATION,
    (4, "date 2025-08-31", None): dict(
        date_status="in the population",
        rf_capture_due_at="2026-11-25 00:00:00+00:00",
    ),
    (4, "date 2025-09-01", None): dict(
        date_status="in the population",
        rf_capture_due_at="2026-11-26 00:00:00+00:00",
    ),
    (4, "date 2025-09-02", None): IN_THE_POPULATION,
    (4, "date 2025-09-03", None): IN_THE_POPULATION,
    (4, "date 2025-09-04", None): dict(
        date_status="in the population",
        rf_capture_due_at="2026-11-29 00:00:00+00:00",
        rf_first_stored_at="2026-11-29 00:05:00+00:00",
    ),
    (4, "date 2025-09-05", None): dict(
        date_status="RF capture due, not held",
        rf_capture_due_at="2026-11-30 00:00:00+00:00",
        rf_first_stored_at=None,
    ),
    (4, "date 2025-09-06", None): dict(
        date_status="RF capture not yet due",
        rf_capture_due_at="2026-12-01 00:00:00+00:00",
    ),
    (4, "date 2025-09-07", None): RF_NOT_YET_DUE,
    (4, "date 2025-09-08", None): RF_NOT_YET_DUE,
    (4, "date 2025-09-09", None): RF_NOT_YET_DUE,
    (4, "date 2025-09-10", None): RF_NOT_YET_DUE,
    (4, "date 2025-09-11", None): RF_NOT_YET_DUE,
    (4, "date 2025-09-12", None): RF_NOT_YET_DUE,
    (4, "date 2025-09-13", None): RF_NOT_YET_DUE,
    (4, "date 2025-09-14", None): RF_NOT_YET_DUE,
    (4, "date 2025-09-15", None): RF_NOT_YET_DUE,
    (4, "date 2025-09-16", None): RF_NOT_YET_DUE,
    (4, "date 2025-09-17", None): RF_NOT_YET_DUE,
    (4, "date 2025-09-18", None): RF_NOT_YET_DUE,
    (4, "date 2025-09-19", None): RF_NOT_YET_DUE,
    (4, "date 2025-09-20", None): dict(
        date_status="RF capture not yet due",
        rf_capture_due_at="2026-12-15 00:00:00+00:00",
    ),
}
# 25 August lies inside the population's first and last date, holds R3 and
# no RF: its row must not be read.
Q2RF_CALENDAR_EXPECTED = {
    (1, "overall", None): dict(
        unit_periods=13,
        only_in_r3=0,
        only_in_rf=0,
        gross_change_mwh=None,
        share_status="population not reached: no result",
        dates=13,
        first_date="2025-08-22",
        last_date="2025-09-04",
    ),
    (5, "capture cutoff", None): dict(instant="2026-11-30 00:00:00+00:00"),
    (5, "population projected at", None): dict(
        position=2, instant="2026-12-15 00:00:00+00:00"
    ),
}

# The project's 30 dates needed, at the NOT_REACHED cutoff (30 November 2026
# 00:00): 22 to 24 August hold both runs, so three.
Q2RF_NOT_REACHED_EXPECTED = {
    (1, "overall", None): dict(
        unit_periods=8,
        gross_change_mwh=None,
        gross_share=None,
        share_status="population not reached: no result",
        dates=3,
    ),
    # 20 and 22 to 24 August and 5 September hold R3 at this cutoff: five
    # dates, not 30, so no reach time lists 5 September. Its RF is due
    # exactly at the cutoff; only "due at or before the cutoff" lists it.
    (
        5,
        "population not projected: fewer dates hold R3 than needed",
        None,
    ): dict(position=2, instant=None),
    (4, "date 2025-09-05", None): dict(
        date_status="RF capture due, not held",
        rf_capture_due_at="2026-11-30 00:00:00+00:00",
        rf_first_stored_at=None,
    ),
}
# No capture cutoff passed (PROJECT_VALUES_VARS: the project's 30 dates and
# 450-day offset, rf_restatement_capture_cutoff null). One unit holds R3 and
# RF on 30 dates from 22 August 2025, every row stored before this check can
# run, so the data reach the population; without a recorded cutoff the gate
# stays shut and only counts come out.
Q2RF_NO_CUTOFF_ROWS = """
insert into synthetic_b1610
select 'T_GAS-1', date '2025-08-22' + day_number, 1, 'R3', 10, '2026-08-24 10:00+00'
from generate_series(0, 29) as day_number;
insert into synthetic_b1610
select 'T_GAS-1', date '2025-08-22' + day_number, 1, 'RF', 11, '2026-08-25 10:00+00'
from generate_series(0, 29) as day_number;
"""
Q2RF_NO_CUTOFF_EXPECTED = {
    (1, "cohort total", None): dict(
        unit_periods=30,
        changed_unit_periods=None,
        gross_change_mwh=None,
        share_status="no capture cutoff passed: no result",
        dates=30,
        first_date="2025-08-22",
        last_date="2025-09-20",
    ),
    (5, "capture cutoff", None): dict(instant=None),
    (5, "population reached at", None): dict(instant="2026-08-25 10:00:00+00:00"),
}
R3_DUE_AT_THE_NOT_REACHED_CUTOFF = """
insert into synthetic_b1610 values
    ('T_GAS-1', '2025-09-05', 1, 'R3', 10, '2026-08-24 10:00+00');
"""


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


# Q27's slot polls: (poll_slot UTC, first poll in its slot, partial, no change).
# The window runs from 1 September 00:00 to analysis_read_until (5 September
# 00:00), both included. In it:
#   A 00:00, 00:30, 01:00 on 1 September, part of a stall that began at 23:30
#     on 31 August: cut at the start; 3 polls, 90 minutes either way; block
#     18:00-23:59 (the stall's first poll, before the window).
#   B 12:00, 12:30 (partial), 13:30, 14:00 on 2 September, with 13:00 lost:
#     4 polls, 120 minutes poll-equivalent, 150 elapsed; block 12:00-17:59,
#     starting on the block's first hour.
#   C 23:30 on 4 September and 00:00 on 5 September, running on past the
#     cutoff: cut at the end; 2 polls, 60 minutes either way; block
#     18:00-23:59.
# Changed polls at 01:30 on 1 September, 14:30 on 2 September (with a second,
# ignored poll in that slot) and 08:00 on 3 September (partial). 12 slot polls
# in the window; 9 inside a stall (0.750000); 2 partial (0.166667), 1 of them
# inside a stall. Durations: poll-equivalent 90, 120, 60 (median 90, longest
# 120); elapsed 90, 150, 60 (median 90, longest 150). The window spans 4 days
# 30 minutes, so 4 days are covered; 4 days hold 193 slots, so 181 have no
# poll. Blocks: 0, 0, 1 (B) and 2 (A, C). The exploratory window, 05:00 to 06:30 on 25 August, holds exactly
# one stall of 4 polls (120 minutes either way; 1.000000 of its 4 slot polls),
# none partial; the stall starts at the window's start and ends at its end, so
# it is not cut.
#
# Every poll here ran on the half-hour schedule, 1.5 seconds after its slot: a
# full poll holds 97 half-hours and a partial one 50, and every poll of a stall
# repeats the one before, so the runs of unchanged polls are the stalls. B,
# with its partial poll inside the window, is the one confirmed freeze. By the
# polls inside the window, A's run is in the 3-poll band (cut at the start), B's
# in 4-5 and C's in 2 (cut at the end), all on the half-hour schedule.
Q27_FIXTURES = """
create temporary table synthetic_revisions (
    poll_slot timestamptz,
    is_first_poll_in_slot boolean,
    is_partial_poll boolean,
    is_no_change_poll boolean,
    retrieved_at timestamptz,
    poll_row_count integer,
    is_unchanged_from_previous_slot boolean
) on commit drop;
insert into synthetic_revisions (
    poll_slot, is_first_poll_in_slot, is_partial_poll, is_no_change_poll
) values
    ('2026-08-25 05:00+00', true, false, true),
    ('2026-08-25 05:30+00', true, false, true),
    ('2026-08-25 06:00+00', true, false, true),
    ('2026-08-25 06:30+00', true, false, true),
    ('2026-08-25 07:00+00', true, false, false),
    ('2026-08-31 23:00+00', true, false, false),
    ('2026-08-31 23:30+00', true, false, true),
    ('2026-09-01 00:00+00', true, false, true),
    ('2026-09-01 00:30+00', true, false, true),
    ('2026-09-01 01:00+00', true, false, true),
    ('2026-09-01 01:30+00', true, false, false),
    ('2026-09-02 12:00+00', true, false, true),
    ('2026-09-02 12:30+00', true, true, true),
    ('2026-09-02 13:30+00', true, false, true),
    ('2026-09-02 14:00+00', true, false, true),
    ('2026-09-02 14:30+00', true, false, false),
    ('2026-09-02 14:30+00', false, false, true),
    ('2026-09-03 08:00+00', true, true, false),
    ('2026-09-04 23:30+00', true, false, true),
    ('2026-09-05 00:00+00', true, false, true),
    ('2026-09-05 00:30+00', true, false, true)
    {after_cutoff_rows};
update synthetic_revisions set
    retrieved_at = poll_slot + interval '1.5 seconds',
    poll_row_count = case when is_partial_poll then 50 else 97 end,
    is_unchanged_from_previous_slot = is_no_change_poll;
"""
# After the cutoff: 00:30 above, then 01:00 and the changed 02:00 poll make
# three slot polls, enough to settle stall C. Without 01:00 the last poll is
# still two hours past the cutoff, but only two slot polls follow it.
Q27_SETTLED_ROWS = (
    ",\n    ('2026-09-05 01:00+00', true, false, true)"
    ",\n    ('2026-09-05 02:00+00', true, false, false)"
)
Q27_GAP_ROWS = ",\n    ('2026-09-05 02:00+00', true, false, false)"
Q27_TABLES = {"int_carbon_forecast__revisions": "pg_temp.synthetic_revisions"}
EXPLORATORY = "exploratory and not part of the result: "
BLOCK = (
    "stalls starting {} UTC (split informed by the long freezes of August and "
    "September 2026 and the stalls of 1 to 24 October 2026)"
)
ON_THE_HALF_HOUR = "on the half-hour"
FIVE_AND_THIRTY_FIVE = ":05 and :35"
ON_THE_HALF_HOUR_BASIS = "decision 016, note of 25 October 2026"
FIVE_AND_THIRTY_FIVE_BASIS = "the reading rule"
CONFIRMED_FREEZES = "confirmed freezes: stalls holding a partial poll inside the window"
CONFIRMED_MEDIAN_POLLS = "confirmed freezes: median duration, poll-equivalent minutes"
CONFIRMED_LONGEST_POLLS = "confirmed freezes: longest duration, poll-equivalent minutes"
CONFIRMED_MEDIAN_ELAPSED = "confirmed freezes: median duration, elapsed minutes"
CONFIRMED_LONGEST_ELAPSED = "confirmed freezes: longest duration, elapsed minutes"
RUN_BANDS = (
    "1 poll",
    "2 polls",
    "3 polls",
    "4-5 polls",
    "6-11 polls",
    "12 or more polls",
)


def minutes_past_slot_key(minutes):
    return (1, f"slot polls made {minutes} minutes past their slot (UTC)", None)


def partial_by_schedule_key(section, prefix, schedule, full_poll_rows):
    return (
        section,
        f"{prefix}partial polls, schedule {schedule} "
        f"(fewer than {full_poll_rows} half-hours)",
        None,
    )


def capture_schedule_key(section, prefix, schedule):
    return (section, f"{prefix}capture schedule {schedule}", None)


def capture_schedule_detail(first_poll, last_poll, full_poll_rows, basis):
    return (
        f"first slot poll {first_poll} UTC; last slot poll {last_poll} UTC; "
        f"full poll {full_poll_rows} half-hours; basis: {basis}"
    )


def run_band_cells(schedule, band, runs, cut_at_start=0, cut_at_end=0):
    key = (
        6,
        f"runs of unchanged slot polls, schedule {schedule}: {band} in the window",
        None,
    )
    detail = (
        f"cut at the window start: {cut_at_start}; cut at the window end: {cut_at_end}"
    )
    return {key: dict(value=str(runs), detail=detail)}


def run_bands_expected(runs_by_schedule_and_band):
    """Every schedule's every band; a band not named has no run."""

    expected = {}
    for schedule in (ON_THE_HALF_HOUR, FIVE_AND_THIRTY_FIVE):
        for band in RUN_BANDS:
            cells = runs_by_schedule_and_band.get((schedule, band), (0, 0, 0))
            runs, cut_at_start, cut_at_end = cells
            expected.update(
                run_band_cells(schedule, band, runs, cut_at_start, cut_at_end)
            )
    return expected


def q27_fixtures(after_cutoff_rows=Q27_SETTLED_ROWS):
    return Q27_FIXTURES.format(after_cutoff_rows=after_cutoff_rows)


Q27_WINDOW_EXPECTED = {
    (1, "window start", None): dict(value=None, detail=STALLS_FROM),
    (1, "first slot poll", None): dict(value=None, detail="2026-09-01 00:00 UTC"),
    (1, "last slot poll", None): dict(value=None, detail="2026-09-05 00:00 UTC"),
    (1, "slot polls", None): dict(value="12", detail=None),
    (1, "slots with no poll", None): dict(value="181", detail=None),
}
Q27_EXPLORATORY_EXPECTED = {
    (5, f"{EXPLORATORY}stalls", None): dict(value="1"),
    (5, f"{EXPLORATORY}median duration, poll-equivalent minutes", None): dict(
        value="120"
    ),
    (5, f"{EXPLORATORY}longest duration, poll-equivalent minutes", None): dict(
        value="120"
    ),
    (5, f"{EXPLORATORY}median duration, elapsed minutes", None): dict(value="120"),
    (5, f"{EXPLORATORY}longest duration, elapsed minutes", None): dict(value="120"),
    (5, f"{EXPLORATORY}share of slot polls inside a stall", None): dict(
        value="1.000000"
    ),
    (5, f"{EXPLORATORY}stalls cut at the window start", None): dict(value="0"),
    (5, f"{EXPLORATORY}stalls cut at the window end", None): dict(value="0"),
    (5, f"{EXPLORATORY}partial polls", None): dict(value="0"),
    (5, f"{EXPLORATORY}share of slot polls that are partial", None): dict(
        value="0.000000"
    ),
    (5, f"{EXPLORATORY}partial polls inside a stall", None): dict(value="0"),
    (5, f"{EXPLORATORY}slot polls", None): dict(value="4"),
    (5, f"{EXPLORATORY}{CONFIRMED_FREEZES}", None): dict(value="0"),
    (5, f"{EXPLORATORY}{CONFIRMED_MEDIAN_POLLS}", None): dict(value=None),
    (5, f"{EXPLORATORY}{CONFIRMED_LONGEST_POLLS}", None): dict(value=None),
    (5, f"{EXPLORATORY}{CONFIRMED_MEDIAN_ELAPSED}", None): dict(value=None),
    (5, f"{EXPLORATORY}{CONFIRMED_LONGEST_ELAPSED}", None): dict(value=None),
    partial_by_schedule_key(5, EXPLORATORY, ON_THE_HALF_HOUR, 97): dict(value="0"),
    partial_by_schedule_key(5, EXPLORATORY, FIVE_AND_THIRTY_FIVE, 96): dict(value="0"),
    capture_schedule_key(5, EXPLORATORY, ON_THE_HALF_HOUR): dict(
        value="4",
        detail=capture_schedule_detail(
            "2026-08-25 05:00:01.500",
            "2026-08-25 06:30:01.500",
            97,
            ON_THE_HALF_HOUR_BASIS,
        ),
    ),
}
Q27_EXPECTED = {
    **Q27_WINDOW_EXPECTED,
    (1, "days covered (4 needed)", None): dict(value="4"),
    (1, "slot polls stored after analysis_read_until (3 needed)", None): dict(
        detail="yes"
    ),
    (1, "read possible", None): dict(detail="yes"),
    minutes_past_slot_key(0): dict(value="12"),
    (2, "stalls", None): dict(value="3"),
    (2, "median duration, poll-equivalent minutes", None): dict(value="90"),
    (2, "longest duration, poll-equivalent minutes", None): dict(value="120"),
    (2, "median duration, elapsed minutes", None): dict(value="90"),
    (2, "longest duration, elapsed minutes", None): dict(value="150"),
    (2, "share of slot polls inside a stall", None): dict(value="0.750000"),
    (2, "stalls cut at the window start", None): dict(value="1"),
    (2, "stalls cut at the window end", None): dict(value="1"),
    (2, CONFIRMED_FREEZES, None): dict(value="1"),
    (2, CONFIRMED_MEDIAN_POLLS, None): dict(value="120"),
    (2, CONFIRMED_LONGEST_POLLS, None): dict(value="120"),
    (2, CONFIRMED_MEDIAN_ELAPSED, None): dict(value="150"),
    (2, CONFIRMED_LONGEST_ELAPSED, None): dict(value="150"),
    (3, "partial polls", None): dict(value="2"),
    (3, "share of slot polls that are partial", None): dict(value="0.166667"),
    (3, "partial polls inside a stall", None): dict(value="1"),
    partial_by_schedule_key(3, "", ON_THE_HALF_HOUR, 97): dict(value="2"),
    partial_by_schedule_key(3, "", FIVE_AND_THIRTY_FIVE, 96): dict(value="0"),
    (4, BLOCK.format("00:00-05:59"), None): dict(value="0"),
    (4, BLOCK.format("06:00-11:59"), None): dict(value="0"),
    (4, BLOCK.format("12:00-17:59"), None): dict(value="1"),
    (4, BLOCK.format("18:00-23:59"), None): dict(value="2"),
    **Q27_EXPLORATORY_EXPECTED,
    **run_bands_expected(
        {
            (ON_THE_HALF_HOUR, "2 polls"): (1, 0, 1),
            (ON_THE_HALF_HOUR, "3 polls"): (1, 1, 0),
            (ON_THE_HALF_HOUR, "4-5 polls"): (1, 0, 0),
        }
    ),
    capture_schedule_key(7, "", ON_THE_HALF_HOUR): dict(
        value="12",
        detail=capture_schedule_detail(
            "2026-09-01 00:00:01.500",
            "2026-09-05 00:00:01.500",
            97,
            ON_THE_HALF_HOUR_BASIS,
        ),
    ),
}
Q27_GATED = ({2, 3, 4, 5, 6, 7}, ("value", "detail"))
# Not reached on days (5 needed), and, with the look-ahead row removed, not
# reached on the look-ahead: either way only section 1 has values; the
# record before the window waits for the gate too.
Q27_NOT_REACHED_EXPECTED = {
    **Q27_WINDOW_EXPECTED,
    (1, "days covered (5 needed)", None): dict(value="4"),
    (1, "read possible", None): dict(detail="no"),
    minutes_past_slot_key(0): dict(value="12"),
}

# The schedule change inside a window, on the project's schedules: window
# from 9 September 2026 00:00 to a cutoff at 12 September 00:00 UTC, the
# record before it 8 September 00:00 to 21:30. Slot polls ran 1.5 seconds
# after their slot until the 10:30 slot on 10 September (polled at
# 10:30:00.7), and at :05 and :35 from 11:05 that day. Each segment is a first
# slot and one letter per consecutive slot: c a changed poll, u an unchanged
# one; four or more unchanged in a row are a stall.
Q27_SCHEDULE_VARS = {
    "analysis_read_until": "2026-09-12 00:00:00+00",
    "forecast_stalls_from": "2026-09-09 00:00:00+00",
    "forecast_stalls_days_needed": 1,
    "forecast_stalls_exploratory_from": "2026-09-08 00:00:00+00",
    "forecast_stalls_exploratory_until": "2026-09-08 21:30:00+00",
}
Q27_SCHEDULE_SEGMENTS = (
    # The record: two changed polls.
    ("2026-09-08 20:00", "cc"),
    # A run of 6 from 23:00 on the 8th, 2 of its polls before the window; then
    # runs of 1, 2, 3, 4, 5, 6, 11 and 12 polls, each after a changed poll.
    (
        "2026-09-08 22:30",
        "c"
        + "u" * 6
        + "c"
        + "u"
        + "c"
        + "u" * 2
        + "c"
        + "u" * 3
        + "c"
        + "u" * 4
        + "c"
        + "u" * 5
        + "c"
        + "u" * 6
        + "c"
        + "u" * 11
        + "c"
        + "u" * 12
        + "c",
    ),
    # A run of 4 across the schedule change: 09:30, 10:00, 10:30:00.7 and 11:05.
    ("2026-09-10 09:00", "c" + "u" * 4 + "c"),
    # A run of 5 from 23:05 on the 11th, 2 of its polls after the cutoff.
    ("2026-09-11 22:30", "c" + "u" * 5 + "c"),
)
SCHEDULE_SWITCH_SLOT = datetime(2026, 9, 10, 11, 0, tzinfo=timezone.utc)
LAST_HALF_HOUR_SCHEDULE_SLOT = datetime(2026, 9, 10, 10, 30, tzinfo=timezone.utc)
# Row counts other than a full poll's (97 on the half-hour, 96 at :05 and
# :35): the run of 6's partial poll, before the window; and 96-row polls on
# 9 September and at 10:30:00.7 on 10 September, partial on the half-hour
# schedule though the model's flag (fewer than 96 rows) is false.
Q27_SCHEDULE_ROW_COUNTS = {
    datetime(2026, 9, 8, 23, 0, tzinfo=timezone.utc): 60,
    datetime(2026, 9, 9, 3, 0, tzinfo=timezone.utc): 96,
    LAST_HALF_HOUR_SCHEDULE_SLOT: 96,
}


def unchanged_run_lengths(letters):
    """For each slot, the length of the run of unchanged polls it is in (0 if changed)."""

    lengths = [0] * len(letters)
    run_start = None
    for index, letter in enumerate(letters + "c"):
        if letter == "u":
            if run_start is None:
                run_start = index
            continue
        if run_start is None:
            continue
        for position in range(run_start, index):
            lengths[position] = index - run_start
        run_start = None
    return lengths


def schedule_window_retrieved_at(slot):
    if slot >= SCHEDULE_SWITCH_SLOT:
        return slot + timedelta(minutes=5)
    if slot == LAST_HALF_HOUR_SCHEDULE_SLOT:
        return slot + timedelta(seconds=0.7)
    return slot + timedelta(seconds=1.5)


def q27_schedule_window_rows():
    rows = []
    for first_slot, letters in Q27_SCHEDULE_SEGMENTS:
        start = datetime.fromisoformat(f"{first_slot}+00:00")
        run_lengths = unchanged_run_lengths(letters)
        for offset, letter in enumerate(letters):
            slot = start + timedelta(minutes=30 * offset)
            full_poll_rows = 96 if slot >= SCHEDULE_SWITCH_SLOT else 97
            row_count = Q27_SCHEDULE_ROW_COUNTS.get(slot, full_poll_rows)
            is_unchanged = letter == "u"
            is_no_change = run_lengths[offset] >= 4
            rows.append(
                f"('{slot.isoformat()}', true, {str(row_count < 96).lower()}, "
                f"{str(is_no_change).lower()}, "
                f"'{schedule_window_retrieved_at(slot).isoformat()}', {row_count}, "
                f"{str(is_unchanged).lower()})"
            )
    return ",\n    ".join(rows)


Q27_FULL_FIXTURES = """
create temporary table synthetic_revisions (
    poll_slot timestamptz,
    is_first_poll_in_slot boolean,
    is_partial_poll boolean,
    is_no_change_poll boolean,
    retrieved_at timestamptz,
    poll_row_count integer,
    is_unchanged_from_previous_slot boolean
) on commit drop;
insert into synthetic_revisions values
    {rows};
"""

# In the window: 48 slot polls on the 9th, 15 on the 10th, 3 on the 11th and
# the one at the cutoff, 67, in 145 slots. Stalls (polls in the window): the
# run of 6 (4, cut at the start), 4, 5, 6, 11, 12, the run across the change
# (4) and the run of 5 (3, cut at the end): poll-equivalent and elapsed
# minutes 120, 120, 150, 180, 330, 360, 120, 90, median 135. 49 stall polls
# of 67. Partial by schedule: the 96-row polls of 9 and 10 September; only the
# second is in a stall, the run across the change, which is the one confirmed
# freeze (the run of 6's partial poll is before the window). Runs by their
# first poll's schedule: on the half-hour 1, 2, 3, then 4-5 four times (the
# run of 6 cut at the start, 4, 5 and the run across the change), 6-11 twice
# and 12 once; at :05 and :35 the run of 5, in the 3-poll band, cut at the end.
Q27_SCHEDULE_EXPECTED = {
    (1, "slot polls", None): dict(value="67"),
    (1, "slots with no poll", None): dict(value="78"),
    (1, "days covered (1 needed)", None): dict(value="3"),
    (1, "read possible", None): dict(detail="yes"),
    minutes_past_slot_key(0): dict(value="61"),
    minutes_past_slot_key(5): dict(value="6"),
    (2, "stalls", None): dict(value="8"),
    (2, "median duration, poll-equivalent minutes", None): dict(value="135"),
    (2, "longest duration, poll-equivalent minutes", None): dict(value="360"),
    (2, "median duration, elapsed minutes", None): dict(value="135"),
    (2, "longest duration, elapsed minutes", None): dict(value="360"),
    (2, "share of slot polls inside a stall", None): dict(value="0.731343"),
    (2, "stalls cut at the window start", None): dict(value="1"),
    (2, "stalls cut at the window end", None): dict(value="1"),
    (2, CONFIRMED_FREEZES, None): dict(value="1"),
    (2, CONFIRMED_MEDIAN_POLLS, None): dict(value="120"),
    (2, CONFIRMED_LONGEST_POLLS, None): dict(value="120"),
    (2, CONFIRMED_MEDIAN_ELAPSED, None): dict(value="120"),
    (2, CONFIRMED_LONGEST_ELAPSED, None): dict(value="120"),
    (3, "partial polls", None): dict(value="2"),
    (3, "share of slot polls that are partial", None): dict(value="0.029851"),
    (3, "partial polls inside a stall", None): dict(value="1"),
    partial_by_schedule_key(3, "", ON_THE_HALF_HOUR, 97): dict(value="2"),
    partial_by_schedule_key(3, "", FIVE_AND_THIRTY_FIVE, 96): dict(value="0"),
    (4, BLOCK.format("00:00-05:59"), None): dict(value="0"),
    (4, BLOCK.format("06:00-11:59"), None): dict(value="3"),
    (4, BLOCK.format("12:00-17:59"), None): dict(value="2"),
    (4, BLOCK.format("18:00-23:59"), None): dict(value="3"),
    **run_bands_expected(
        {
            (ON_THE_HALF_HOUR, "1 poll"): (1, 0, 0),
            (ON_THE_HALF_HOUR, "2 polls"): (1, 0, 0),
            (ON_THE_HALF_HOUR, "3 polls"): (1, 0, 0),
            (ON_THE_HALF_HOUR, "4-5 polls"): (4, 1, 0),
            (ON_THE_HALF_HOUR, "6-11 polls"): (2, 0, 0),
            (ON_THE_HALF_HOUR, "12 or more polls"): (1, 0, 0),
            (FIVE_AND_THIRTY_FIVE, "3 polls"): (1, 0, 1),
        }
    ),
    capture_schedule_key(7, "", ON_THE_HALF_HOUR): dict(
        value="61",
        detail=capture_schedule_detail(
            "2026-09-09 00:00:01.500",
            "2026-09-10 10:30:00.700",
            97,
            ON_THE_HALF_HOUR_BASIS,
        ),
    ),
    capture_schedule_key(7, "", FIVE_AND_THIRTY_FIVE): dict(
        value="6",
        detail=capture_schedule_detail(
            "2026-09-10 11:05:00.000",
            "2026-09-12 00:05:00.000",
            96,
            FIVE_AND_THIRTY_FIVE_BASIS,
        ),
    ),
    (5, f"{EXPLORATORY}slot polls", None): dict(value="2"),
    (5, f"{EXPLORATORY}stalls", None): dict(value="0"),
    capture_schedule_key(5, EXPLORATORY, ON_THE_HALF_HOUR): dict(
        value="2",
        detail=capture_schedule_detail(
            "2026-09-08 20:00:01.500",
            "2026-09-08 20:30:01.500",
            97,
            ON_THE_HALF_HOUR_BASIS,
        ),
    ),
}

# The record's ends and the window's start on the project's values: slot
# polls at 23:30 on 16 August (not in the record), 00:00 on 17 August and
# 23:35 on 31 October (slot 23:30, in it), 00:05 on 1 November (in the window,
# not the record), then 23:35 that day and 00:05 on 2 November (the cutoff's
# slot), and three after the cutoff.
Q27_PROJECT_VALUES_ROWS = """
    ('2026-08-16 23:30+00', true, false, false, '2026-08-16 23:30:01.5+00', 97, false),
    ('2026-08-17 00:00+00', true, false, false, '2026-08-17 00:00:01.5+00', 97, false),
    ('2026-10-31 23:30+00', true, false, false, '2026-10-31 23:35+00', 96, false),
    ('2026-11-01 00:00+00', true, false, false, '2026-11-01 00:05+00', 96, false),
    ('2026-11-01 23:30+00', true, false, false, '2026-11-01 23:35+00', 96, false),
    ('2026-11-02 00:00+00', true, false, false, '2026-11-02 00:05+00', 96, false),
    ('2026-11-02 00:30+00', true, false, false, '2026-11-02 00:35+00', 96, false),
    ('2026-11-02 01:00+00', true, false, false, '2026-11-02 01:05+00', 96, false),
    ('2026-11-02 01:30+00', true, false, false, '2026-11-02 01:35+00', 96, false)"""
Q27_PROJECT_VALUES_EXPECTED = {
    (1, "window start", None): dict(detail="2026-11-01 00:00:00+00"),
    (1, "slot polls", None): dict(value="3"),
    (1, "days covered (1 needed)", None): dict(value="1"),
    (1, "read possible", None): dict(detail="yes"),
    (5, f"{EXPLORATORY}slot polls", None): dict(value="2"),
    capture_schedule_key(5, EXPLORATORY, ON_THE_HALF_HOUR): dict(
        value="1",
        detail=capture_schedule_detail(
            "2026-08-17 00:00:01.500",
            "2026-08-17 00:00:01.500",
            97,
            ON_THE_HALF_HOUR_BASIS,
        ),
    ),
    capture_schedule_key(5, EXPLORATORY, FIVE_AND_THIRTY_FIVE): dict(
        value="1",
        detail=capture_schedule_detail(
            "2026-10-31 23:35:00.000",
            "2026-10-31 23:35:00.000",
            96,
            FIVE_AND_THIRTY_FIVE_BASIS,
        ),
    ),
}
Q27_NO_LOOK_AHEAD_EXPECTED = {
    (1, "slot polls stored after analysis_read_until (3 needed)", None): dict(
        detail="no"
    ),
    (1, "read possible", None): dict(detail="no"),
}


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


def q4_fixtures(
    a_bottom,
    b_bottom,
    a_tenth,
    stale_b_half_hours=0,
    extra_days="",
    b39_error=39,
    settlement_runs="",
):
    return Q4_FIXTURES.format(
        a_bottom=a_bottom,
        b_bottom=b_bottom,
        a_tenth=a_tenth,
        stale_b_half_hours=stale_b_half_hours,
        extra_days=extra_days,
        b39_error=b39_error,
        settlement_runs=settlement_runs,
    )


# A matched day before A (31 August) whose every final forecast is stale:
# it counts towards the gate but holds no half-hour for the quintiles, so
# the bootstrap must resample only A and B and give the same interval.
ALL_STALE_DAY = ", (date '2026-08-31')"


def q2rf_fixtures(extra_cohort_rows="", b1610_rows=Q2RF_B1610_ROWS, changes=""):
    return Q2RF_FIXTURES.format(
        extra_cohort_rows=extra_cohort_rows, b1610_rows=b1610_rows, changes=changes
    )


def q2rf_section(run, section):
    """The run with only the rows of one section."""

    rows, error = run
    in_section = {}
    for key, cells in rows.items():
        if cells["section"] == section:
            in_section[key] = cells
    return in_section, error


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


def usable_draws_without_b_twice(seed=BOOTSTRAP_SEED):
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


def section_order_mismatches(title, run, section, expected_keys):
    """The section's rows come out in the expected order."""

    rows, error = run
    if error is not None:
        return []
    found_keys = []
    for key in rows:
        if key[0] == section:
            found_keys.append(key)
    if found_keys != list(expected_keys):
        return [f"{title}: section {section} comes out as {found_keys}"]
    return []


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


def q4_mismatches(conn, reached, not_reached, boundary, time_zone):
    title = f"[{time_zone}] Q4"
    usable_draws = usable_draws_without_b_twice()
    main_run = run_analysis(
        conn, q4_fixtures(1, 1, 3), reached, Q4, Q4_TABLES, time_zone
    )
    found = mismatches(title, main_run, Q4_EXPECTED)
    found += unexpected_rows(title, main_run, Q4_EXPECTED)
    found += section_order_mismatches(title, main_run, 1, Q4_SECTION_ONE_ORDER)
    second_run = run_analysis(
        conn, q4_fixtures(1, 1, 3), reached, Q4, Q4_TABLES, time_zone
    )
    if second_run != main_run:
        found.append(f"{title}: a second run gives different rows")

    top_tie_run = run_analysis(
        conn, q4_fixtures(1, 1, 3, b39_error=38), reached, Q4, Q4_TABLES, time_zone
    )
    found += mismatches(f"{title} top-edge tie", top_tie_run, Q4_TOP_TIE_SPLIT_EXPECTED)

    unavailable_expected = dict(Q4_UNAVAILABLE_EXPECTED)
    unavailable_expected[DRAWS] = dict(value=str(usable_draws), result="unavailable")
    unavailable_run = run_analysis(
        conn, q4_fixtures(1, 0, 1), reached, Q4, Q4_TABLES, time_zone
    )
    found += mismatches(f"{title} unavailable", unavailable_run, unavailable_expected)

    boundary_draws = (
        1,
        f"bootstrap draws with a ratio ({usable_draws} of {BOOTSTRAP_DRAWS} needed)",
        None,
    )
    boundary_expected = dict(Q4_BOUNDARY_EXPECTED)
    boundary_expected[boundary_draws] = dict(
        value=str(usable_draws), result="available"
    )
    boundary_run = run_analysis(
        conn, q4_fixtures(1, 0, 1), boundary, Q4, Q4_TABLES, time_zone
    )
    found += mismatches(f"{title} boundary", boundary_run, boundary_expected)

    not_applicable_run = run_analysis(
        conn, q4_fixtures(0, 0, 0), reached, Q4, Q4_TABLES, time_zone
    )
    found += mismatches(
        f"{title} not applicable", not_applicable_run, Q4_NOT_APPLICABLE_EXPECTED
    )

    early_run = run_analysis(
        conn, q4_fixtures(1, 1, 3), not_reached, Q4, Q4_TABLES, time_zone
    )
    found += mismatches(f"{title} early", early_run, Q4_NOT_REACHED_EXPECTED)
    found += gated_values_left_null(f"{title} early", early_run, Q4_GATED)
    found += gated_values_left_null(f"{title} early", early_run, Q4_QUINTILES_GATED)

    # Three stale final forecasts on B, which has 46 half-hours with both
    # values: they leave the quintiles (48 + 46 - 3 = 91 half-hours), but B
    # stays a matched day.
    stale_run = run_analysis(
        conn,
        q4_fixtures(1, 1, 3, stale_b_half_hours=3),
        reached,
        Q4,
        Q4_TABLES,
        time_zone,
    )
    found += mismatches(
        f"{title} stale forecasts on a matched day",
        stale_run,
        {
            POPULATION: dict(days=2, half_hours=91),
            LEFT_OUT: dict(half_hours=4),
            **stale_finals_expected({"Tuesday": 48, "Wednesday": 43}, {"Wednesday": 3}),
        },
    )

    all_stale_day_run = run_analysis(
        conn,
        q4_fixtures(1, 1, 3, extra_days=ALL_STALE_DAY),
        reached,
        Q4,
        Q4_TABLES,
        time_zone,
    )
    found += mismatches(
        f"{title} a matched day with no fresh forecast",
        all_stale_day_run,
        {
            POPULATION: dict(days=3, half_hours=94),
            NO_FRESH_DAYS: dict(days=1),
            LOW: Q4_EXPECTED[LOW],
            HIGH: Q4_EXPECTED[HIGH],
            # Monday 31 August: its first two half-hours start on Sunday in UTC.
            **stale_finals_expected({"Tuesday": 48, "Wednesday": 46}, {"Monday": 48}),
        },
    )

    on_ii_run = run_analysis(
        conn,
        q4_fixtures(1, 1, 3, settlement_runs=Q4_ON_II_RUNS),
        reached,
        Q4,
        Q4_TABLES,
        time_zone,
    )
    on_ii_title = f"{title} dates on the one-week run"
    found += mismatches(on_ii_title, on_ii_run, Q4_ON_II_EXPECTED)
    found += gated_values_left_null(on_ii_title, on_ii_run, Q4_GATED)
    found += unexpected_left_out_dates(on_ii_title, on_ii_run, Q4_ON_II_EXPECTED)

    for case_name, rerun_rows in Q4_RERUNS:
        rerun_run = run_analysis(
            conn,
            q4_fixtures(1, 1, 3, settlement_runs=rerun_rows),
            reached,
            Q4,
            Q4_TABLES,
            time_zone,
        )
        rerun_title = f"{title} rerun after {case_name}"
        found += mismatches(rerun_title, rerun_run, rerun_expected())
        found += unexpected_rows(rerun_title, rerun_run, rerun_expected())
    return found


def unexpected_left_out_dates(title, run, expected):
    """Left-out date rows the worked values do not list."""

    rows, error = run
    if error is not None:
        return []
    found = []
    for key in rows:
        if key[1].startswith("left out, on the one-week run") and key not in expected:
            found.append(f"{title}: unexpected row {key}")
    return found


def compile_q4_other_cutoffs():
    """The Q4 manifests of Q4_OTHER_CUTOFF_VARS, by the same names."""

    manifests = {}
    for case_name, dbt_vars in Q4_OTHER_CUTOFF_VARS.items():
        manifests[case_name] = compile_analyses(dbt_vars, selected=(Q4,))
    return manifests


def q4_other_cutoff_mismatches(conn, manifests, time_zone):
    """No result without analysis_read_until or the capture cutoff; the date
    the gate is reached against analysis_read_until's London date."""

    title = f"[{time_zone}] Q4"
    found = []
    refusals = (
        ("without analysis_read_until", Q4_WITHOUT_READ_UNTIL_EXPECTED),
        ("without the capture cutoff", Q4_WITHOUT_CAPTURE_CUTOFF_EXPECTED),
    )
    for case_name, expected in refusals:
        run = run_analysis(
            conn, q4_fixtures(1, 1, 3), manifests[case_name], Q4, Q4_TABLES, time_zone
        )
        found += mismatches(f"{title} {case_name}", run, expected)
        found += gated_values_left_null(f"{title} {case_name}", run, Q4_GATED)
        found += gated_values_left_null(f"{title} {case_name}", run, Q4_QUINTILES_GATED)

    gate_days = (
        (
            "cutoff on B's settlement date",
            "the settlement date of analysis_read_until",
        ),
        (
            "cutoff at the start of 3 September in London",
            "not the settlement date of analysis_read_until, 2026-09-03",
        ),
    )
    for case_name, result in gate_days:
        run = run_analysis(
            conn, q4_fixtures(1, 1, 3), manifests[case_name], Q4, Q4_TABLES, time_zone
        )
        found += mismatches(
            f"{title} {case_name}",
            run,
            {gate_day_key(2): dict(first_date="2026-09-02", result=result)},
        )
    return found


def q2rf_mismatches(conn, reached, not_reached, project_values, time_zone):
    title = f"[{time_zone}] Q2-RF"
    main_run = run_analysis(
        conn, q2rf_fixtures(), reached, Q2RF, Q2RF_TABLES, time_zone
    )
    found = mismatches(title, main_run, Q2RF_EXPECTED)
    found += mismatches(title, main_run, Q2RF_POPULATION)
    found += unexpected_rows(title, main_run, Q2RF_EXPECTED)
    found += section_order_mismatches(title, main_run, 1, Q2RF_MEASURES_ORDER)
    found += gated_values_left_null(title, main_run, Q2RF_NO_ENERGY)

    late_run = run_analysis(
        conn,
        q2rf_fixtures(changes=STORED_JUST_AFTER_THE_CUTOFF),
        reached,
        Q2RF,
        Q2RF_TABLES,
        time_zone,
    )
    found += mismatches(f"{title} late", late_run, Q2RF_LATE_EXPECTED)

    one_day_earlier_run = run_analysis(
        conn,
        q2rf_fixtures(changes=ONE_DAY_EARLIER),
        reached,
        Q2RF,
        Q2RF_TABLES,
        time_zone,
    )
    earlier_title = f"{title} one day earlier"
    found += mismatches(earlier_title, one_day_earlier_run, Q2RF_MEASURES)
    found += mismatches(
        earlier_title, one_day_earlier_run, Q2RF_ONE_DAY_EARLIER_EXPECTED
    )
    found += unexpected_rows(
        earlier_title,
        one_day_earlier_run,
        {**Q2RF_MEASURES, **Q2RF_ONE_DAY_EARLIER_EXPECTED},
    )

    clock_change_run = run_analysis(
        conn,
        q2rf_fixtures(b1610_rows=Q2RF_CLOCK_CHANGE_ROWS),
        reached,
        Q2RF,
        Q2RF_TABLES,
        time_zone,
    )
    found += mismatches(
        f"{title} clock changes", clock_change_run, Q2RF_CLOCK_CHANGE_EXPECTED
    )

    early_run = run_analysis(
        conn,
        q2rf_fixtures(changes=R3_DUE_AT_THE_NOT_REACHED_CUTOFF),
        not_reached,
        Q2RF,
        Q2RF_TABLES,
        time_zone,
    )
    found += mismatches(f"{title} early", early_run, Q2RF_NOT_REACHED_EXPECTED)
    found += gated_values_left_null(f"{title} early", early_run, Q2RF_GATED)
    found += gated_values_left_null(f"{title} early", early_run, Q2RF_NO_ENERGY)

    calendar_run = run_analysis(
        conn,
        q2rf_fixtures(b1610_rows=Q2RF_CALENDAR_ROWS),
        not_reached,
        Q2RF,
        Q2RF_TABLES,
        time_zone,
    )
    calendar_title = f"{title} production calendar"
    found += mismatches(calendar_title, calendar_run, Q2RF_CALENDAR_EXPECTED)
    found += mismatches(calendar_title, calendar_run, Q2RF_CALENDAR_DATES)
    found += unexpected_rows(
        calendar_title, q2rf_section(calendar_run, 4), Q2RF_CALENDAR_DATES
    )
    found += gated_values_left_null(calendar_title, calendar_run, Q2RF_GATED)
    found += gated_values_left_null(calendar_title, calendar_run, Q2RF_NO_ENERGY)

    no_cutoff_run = run_analysis(
        conn,
        q2rf_fixtures(b1610_rows=Q2RF_NO_CUTOFF_ROWS),
        project_values,
        Q2RF,
        Q2RF_TABLES,
        time_zone,
    )
    no_cutoff_title = f"{title} no cutoff"
    found += mismatches(no_cutoff_title, no_cutoff_run, Q2RF_NO_CUTOFF_EXPECTED)
    found += gated_values_left_null(no_cutoff_title, no_cutoff_run, Q2RF_GATED)
    found += gated_values_left_null(no_cutoff_title, no_cutoff_run, Q2RF_NO_ENERGY)

    two_groups_run = run_analysis(
        conn,
        q2rf_fixtures(TWO_GROUPS_FOR_ONE_ID),
        reached,
        Q2RF,
        Q2RF_TABLES,
        time_zone,
    )
    found += mismatches(
        f"{title} two groups",
        two_groups_run,
        {
            REPEATED_ID: dict(units=1),
            Q2RF_OVERALL: Q2RF_TWO_GROUPS_OVERALL,
            Q2RF_COHORT_TOTAL: Q2RF_TWO_GROUPS_COHORT_TOTAL,
        },
    )
    return found


def q25_mismatches(conn, reached, time_zone):
    query = on_fixtures(reached, Q25, Q25_TABLES)
    run = run_on_fixtures(conn, Q25_FIXTURES, query, time_zone, Q25_KEY)
    return mismatches(f"[{time_zone}] Q25", run, Q25_EXPECTED)


def q27_mismatches(conn, reached, not_reached, time_zone):
    title = f"[{time_zone}] Q27"
    main_run = run_analysis(conn, q27_fixtures(), reached, Q27, Q27_TABLES, time_zone)
    found = mismatches(title, main_run, Q27_EXPECTED)
    found += unexpected_rows(title, main_run, Q27_EXPECTED)

    early_run = run_analysis(
        conn, q27_fixtures(), not_reached, Q27, Q27_TABLES, time_zone
    )
    found += mismatches(f"{title} early", early_run, Q27_NOT_REACHED_EXPECTED)
    found += gated_values_left_null(f"{title} early", early_run, Q27_GATED)

    no_look_ahead_run = run_analysis(
        conn, q27_fixtures(Q27_GAP_ROWS), reached, Q27, Q27_TABLES, time_zone
    )
    found += mismatches(
        f"{title} no look-ahead", no_look_ahead_run, Q27_NO_LOOK_AHEAD_EXPECTED
    )
    found += gated_values_left_null(
        f"{title} no look-ahead", no_look_ahead_run, Q27_GATED
    )
    return found


def q27_schedule_change_mismatches(conn, schedule_window, time_zone):
    title = f"[{time_zone}] Q27 schedule change"
    fixtures = Q27_FULL_FIXTURES.format(rows=q27_schedule_window_rows())
    run = run_analysis(conn, fixtures, schedule_window, Q27, Q27_TABLES, time_zone)
    found = mismatches(title, run, Q27_SCHEDULE_EXPECTED)
    second_run = run_analysis(
        conn, fixtures, schedule_window, Q27, Q27_TABLES, time_zone
    )
    if second_run != run:
        found.append(f"{title}: a second run gives different rows")
    return found


def q27_project_values_mismatches(conn, project_values, time_zone):
    title = f"[{time_zone}] Q27 project values"
    fixtures = Q27_FULL_FIXTURES.format(rows=Q27_PROJECT_VALUES_ROWS)
    run = run_analysis(conn, fixtures, project_values, Q27, Q27_TABLES, time_zone)
    return mismatches(title, run, Q27_PROJECT_VALUES_EXPECTED)


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


# Q26 on hand-worked unit-periods. Cohort units: B1 (other), W1 (wind), G1
# (gas) and H1 (hydro) are active, with a PN above 0 at 22:30 UTC on
# 26 October 2025; IF1 (INTFR) and IE1 (INTELE) are interconnectors; Z1 never
# has a PN or metered value above 0 (idle); E1's PN is above 0 only at 22:30
# UTC on 24 October 2025, half an hour before the window opens at the first
# counted settlement date (25 October 2025 00:00 BST, 24 October 23:00 UTC),
# and on 6 September 2026, after the cutoff.
#
# QAS captures, by the UTC date of each settlement date's latest capture:
# 23 October 2025 12 November (20 days: captured, not counted, so the window
# does not open there); 24 October none (not captured); 25 October latest
# 29 November 00:00
# (exactly 35 days; an earlier capture at 14 days); 26 October (50 periods)
# 5 December (40 days); 28 March 2026 latest 1 May 23:59:59.9 UTC (34 days,
# though 2 May in London); 29 March (46 periods) latest 4 May (36 days; an
# earlier capture at 14 days with other values); 30 March 9 May (40 days);
# 6 September 2026, after the cutoff, 20 October (44 days).
#
# Determinable unit-periods with a PN: (unit, date, period, PN, metered,
# instructed deviation, unexplained before ABSVD, ABSVD). Unexplained after
# ABSVD is before plus ABSVD.
Q26 = "q26_instructed_and_absvd_share"
Q26_FIXTURES = """
create temporary table synthetic_shortfall (
    national_grid_bm_unit text,
    settlement_date date,
    settlement_period integer,
    period_start_utc timestamptz,
    determinability text,
    pn_mwh numeric,
    metered_mwh numeric,
    instructed_deviation_mwh numeric,
    unexplained_shortfall_mwh numeric,
    absvd_mwh numeric,
    unexplained_after_absvd_mwh numeric
) on commit drop;
create temporary table synthetic_cohort (
    national_grid_bm_unit text,
    elexon_bm_unit text,
    fuel_type text,
    fuel_group text
) on commit drop;
create temporary table synthetic_pn_periods (
    national_grid_bm_unit text,
    settlement_date date,
    period_start_utc timestamptz,
    pn_mwh numeric,
    mapping_status text
) on commit drop;
create temporary table synthetic_metered_periods (
    bm_unit text,
    settlement_date date,
    period_start_utc timestamptz,
    first_quantity_mwh numeric,
    latest_quantity_mwh numeric,
    mapping_status text,
    mapped_national_grid_bm_unit text
) on commit drop;
create temporary table synthetic_qas (
    national_grid_bm_unit text,
    bm_unit text,
    settlement_date date,
    settlement_period integer,
    qas_mwh numeric,
    retrieved_at timestamptz
) on commit drop;

insert into synthetic_cohort values
    ('B1', 'E_B1', 'OTHER', 'other'), ('W1', 'E_W1', 'WIND', 'wind'),
    ('G1', 'E_G1', 'CCGT', 'gas'), ('H1', 'E_H1', 'NPSHYD', 'hydro'),
    ('IF1', 'E_IF1', 'INTFR', 'interconnector'),
    ('IE1', 'E_IE1', 'INTELE', 'unclassified'),
    ('Z1', 'E_Z1', 'CCGT', 'gas'), ('E1', 'E_E1', 'CCGT', 'gas');

insert into synthetic_shortfall (
    national_grid_bm_unit, settlement_date, settlement_period, determinability,
    pn_mwh, metered_mwh, instructed_deviation_mwh, unexplained_shortfall_mwh,
    absvd_mwh
) values
    ('B1', '2025-10-24', 10, 'determinable', 1, 1, 1, 3, null),
    ('B1', '2025-10-25', 10, 'determinable', 1, 1, 2, 10, -4),
    ('B1', '2025-10-26', 48, 'determinable', 1, 1, 0, 8, -5),
    ('B1', '2025-10-26', 49, 'determinable', 1, 1, 1, -6, 2),
    ('B1', '2025-10-26', 50, 'determinable', 0, -4, 0, 4, 0),
    ('B1', '2026-03-28', 10, 'determinable', 1, 1, 5, 7, -1),
    ('B1', '2026-03-29', 10, 'determinable', 1, 1, 3, 5, -2),
    ('B1', '2026-03-30', 46, 'determinable', 1, 1, 1, 9, -3),
    ('B1', '2026-03-30', 47, 'determinable', 1, 1, 0, 2, 1),
    ('B1', '2026-09-06', 10, 'determinable', 1, 1, 1, 1, null),
    ('W1', '2025-10-26', 48, 'determinable', 1, 1, 4, 6, 2),
    ('W1', '2026-03-30', 46, 'determinable', 1, 1, 2, -5, 1),
    ('G1', '2025-10-26', 48, 'determinable', 0, 0, 0, 0, 0),
    ('G1', '2026-03-30', 46, 'determinable', 1, 1, 6, 2, 0),
    ('H1', '2026-03-28', 10, 'determinable', 1, 1, 1, 1, 0),
    ('IF1', '2025-10-26', 48, 'determinable', 1, 1, 9, 9, 9),
    ('IE1', '2026-03-30', 46, 'determinable', 1, 1, 7, 7, 7),
    ('Z1', '2025-10-26', 48, 'determinable', 0, 0, 0, 0, 0),
    ('E1', '2026-03-30', 46, 'determinable', 0, 0, 0, 0, 0);
update synthetic_shortfall set
    period_start_utc = (settlement_date::timestamp at time zone 'Europe/London')
        + (settlement_period - 1) * interval '30 minutes',
    unexplained_after_absvd_mwh = unexplained_shortfall_mwh + absvd_mwh;

insert into synthetic_pn_periods
select
    unit,
    activity_date,
    (activity_date::timestamp at time zone 'Europe/London')
        + (period - 1) * interval '30 minutes',
    pn_mwh,
    'mapped'
from (
    values
        ('B1', date '2025-10-26', 48, 5), ('W1', date '2025-10-26', 48, 5),
        ('G1', date '2025-10-26', 48, 5), ('H1', date '2025-10-26', 48, 5),
        ('IF1', date '2025-10-26', 48, 5), ('IE1', date '2025-10-26', 48, 5),
        ('Z1', date '2025-10-26', 48, 0), ('E1', date '2025-10-24', 48, 5),
        ('E1', date '2026-09-06', 10, 5)
) as activity (unit, activity_date, period, pn_mwh);

insert into synthetic_qas values
    ('B1', 'E_B1', '2025-10-23', 10, -3, '2025-11-12 10:00:00+00'),
    ('B1', 'E_B1', '2025-10-25', 10, -4, '2025-11-08 10:00:00+00'),
    ('B1', 'E_B1', '2025-10-25', 48, -9, '2025-11-08 10:00:00+00'),
    ('W1', 'E_W1', '2025-10-25', 48, 3, '2025-11-08 10:00:00+00'),
    ('B1', 'E_B1', '2025-10-25', 10, -4, '2025-11-29 00:00:00+00'),
    ('B1', 'E_B1', '2025-10-25', 48, -1, '2025-11-29 00:00:00+00'),
    ('B1', 'E_B1', '2025-10-26', 48, -5, '2025-12-05 10:00:00+00'),
    ('B1', 'E_B1', '2025-10-26', 49, 2, '2025-12-05 10:00:00+00'),
    ('W1', 'E_W1', '2025-10-26', 48, 2, '2025-12-05 10:00:00+00'),
    ('IF1', 'E_IF1', '2025-10-26', 48, 9, '2025-12-05 10:00:00+00'),
    ('B1', 'E_B1', '2026-03-28', 10, -1, '2026-05-01 23:59:59.9+00'),
    ('B1', 'E_B1', '2026-03-29', 10, -2, '2026-04-12 10:00:00+00'),
    ('B1', 'E_B1', '2026-03-29', 46, -2, '2026-04-12 10:00:00+00'),
    ('B1', 'E_B1', '2026-03-29', 10, -2, '2026-05-04 10:00:00+00'),
    ('B1', 'E_B1', '2026-03-29', 46, -6, '2026-05-04 10:00:00+00'),
    ('W1', 'E_W1', '2026-03-29', 46, 4, '2026-05-04 10:00:00+00'),
    ('B1', 'E_B1', '2026-03-30', 46, -3, '2026-05-09 10:00:00+00'),
    ('B1', 'E_B1', '2026-03-30', 47, 1, '2026-05-09 10:00:00+00'),
    ('W1', 'E_W1', '2026-03-30', 46, 1, '2026-05-09 10:00:00+00'),
    ('IE1', 'E_IE1', '2026-03-30', 46, 7, '2026-05-09 10:00:00+00'),
    ('B1', 'E_B1', '2026-09-06', 10, 0, '2026-10-20 10:00:00+00');
"""
Q26_TABLES = {
    "int_shortfall_by_unit_period": "pg_temp.synthetic_shortfall",
    "int_elexon__bm_unit_cohort": "pg_temp.synthetic_cohort",
    "int_elexon__pn_period_mapped": "pg_temp.synthetic_pn_periods",
    "int_elexon__b1610_period_mapped": "pg_temp.synthetic_metered_periods",
    "stg_elexon__qas": "pg_temp.synthetic_qas",
}
Q26_KEY = ("section", "item", "fuel_group", "settlement_month")
Q26_RESULT = "explained share with ABSVD: the registered result"
Q26_WITH_ABSVD = "unit-periods with any ABSVD (value: their share)"
Q26_UNCAPTURED = "unit-periods left out: settlement date not captured"
Q26_YOUNG = (
    "unit-periods left out: latest capture less than 35 days after the settlement date"
)
Q26_FALL = (
    "fall in unexplained energy once ABSVD is counted: not called "
    "pre-registered (the exploration of 11 October 2026)"
)
Q26_MONTH = (
    "by calendar month of the settlement date: informed by the exploration of "
    "11 October 2026 (falls in winter, small rises in summer)"
)
Q26_PLACEBO = (
    "placebo: ABSVD of the same unit and settlement period on the previous "
    "settlement date"
)
Q26_REAL_ON_PLACEBO = "real figures on the placebo's unit-periods"
Q26_NO_PARTNER = (
    "left out of the placebo: the previous settlement date has no such "
    "settlement period"
)
Q26_PARTNER_UNCAPTURED = (
    "left out of the placebo: the previous settlement date was not captured"
)
Q26_PARTNER_YOUNG = (
    "left out of the placebo: the previous settlement date's latest capture "
    "is less than 35 days after it"
)
Q26_ALL = "all fuel groups"
Q26_NOT_APPLICABLE = "N/A"
Q26_PLACEBO_DETAIL = (
    "no test: a placebo fall near the real fall says ABSVD's apparent effect is "
    "not specific to the half-hour it belongs to"
)
Q26_CAPTURED_DATES = "settlement dates captured"
Q26_COUNTED_DATES = (
    "settlement dates counted: latest capture's UTC date at least 35 days after "
    "the settlement date"
)
Q26_LATEST_CAPTURE = (
    "each unit-period's ABSVD is from the latest capture holding it; not called final"
)
Q26_YOUNG_DATES = (
    "settlement dates left out: latest capture less than 35 days after the "
    "settlement date"
)
Q26_ACTIVE_UNITS = "cohort units: active in the window (value: units)"
Q26_INTERCONNECTOR_UNITS = (
    "cohort units: interconnectors, fuel type beginning INT, left out (value: units)"
)
Q26_INTELE_UNITS = "of which INTELE, an interconnector by assumption (value: units)"
Q26_INACTIVE_UNITS = "cohort units: inactive in the window, left out (value: units)"
Q26_INTERCONNECTOR_PERIODS = "unit-periods left out: interconnector units"
Q26_INACTIVE_PERIODS = "unit-periods left out: inactive units"
Q26_IDLE = "idle unit-periods in the result: PN 0 and metered 0 (value: their share)"
OCTOBER_2025 = "2025-10-01"
MARCH_2026 = "2026-03-01"


def q26_figures(
    unit_periods,
    instructed=None,
    absvd=None,
    before=None,
    after=None,
    share=None,
    fall=None,
    fall_share=None,
    detail=None,
):
    """A figure row's cells: n, the four absolute sums, the share and the fall."""

    return dict(
        unit_periods=unit_periods,
        abs_instructed_deviation_mwh=instructed,
        abs_absvd_mwh=absvd,
        abs_unexplained_before_absvd_mwh=before,
        abs_unexplained_after_absvd_mwh=after,
        explained_share_with_absvd=share,
        fall_mwh=fall,
        fall_share=fall_share,
        detail=detail,
    )


def q26_dates(first_date, last_date):
    return dict(first_date=first_date, last_date=last_date)


# Worked by hand from the rows above. In the result (active units, captured,
# at least 35 days): other B1 on 25 October p10, 26 October p48 to p50,
# 29 March p10, 30 March p46 and p47 (n 7); wind W1's two rows; gas G1's two
# rows (Z1 idle and E1 inactive are left out); hydro H1's only row is on the
# 34-day date. Other: I = 2+0+1+0+3+1+0 = 7, A = 4+5+2+0+2+3+1 = 17,
# U = 10+8+6+4+5+9+2 = 44, U' = 6+3+4+4+3+6+3 = 29; share 24/53, Q12 7/51,
# fall 15, 15/44. Wind: I 6, A 3, U 11, U' 12; share 9/21, Q12 6/17, fall -1,
# -1/11. Gas: I 6, A 0, U 2, U' 2; share and Q12 6/8, fall 0.
#
# Placebo, other: 25 October p10's previous date is not captured; 26 October
# p49 and p50 have no partner on the 48-period 25 October; 29 March p10's
# previous date is 34 days old; 30 March p47 has no partner on the 46-period
# 29 March. Kept: 26 October p48 (25 October's latest -1, not the earlier -9)
# and 30 March p46 (29 March's latest -6, not -2): I 1, A 1+6 = 7, U 17,
# U' 7+3 = 10; real A 8, U' 9. Wind: 26 October p48 takes 3 from 25 October's
# earlier capture, the latest holding it (the latest capture has no W1 row)
# (U' 9); 30 March p46 takes 4 (U' 1): I 6, A 7, U 11, U' 10. Gas: no QAS
# rows, so 0: I 6, A 0, U 2, U' 2. All: I 13, A 14, U 30, U' 22; real A 11,
# U' 23.
Q26_EXPECTED = {
    (1, Q26_RESULT, "other", None): dict(
        **q26_figures(7, "7.0", "17.0", "44.0", "29.0", "0.452830"),
        **q26_dates("2025-10-25", "2026-03-30"),
        q12_instructed_share_same_periods="0.137255",
    ),
    (1, Q26_RESULT, "wind", None): dict(
        **q26_figures(2, "6.0", "3.0", "11.0", "12.0", "0.428571"),
        **q26_dates("2025-10-26", "2026-03-30"),
        q12_instructed_share_same_periods="0.352941",
    ),
    (1, Q26_RESULT, "gas", None): dict(
        **q26_figures(2, "6.0", "0.0", "2.0", "2.0", "0.750000"),
        **q26_dates("2025-10-26", "2026-03-30"),
        q12_instructed_share_same_periods="0.750000",
    ),
    (1, Q26_RESULT, "hydro", None): dict(
        **q26_figures(0, "0.0", "0.0", "0.0", "0.0", None, detail=Q26_NOT_APPLICABLE),
        **q26_dates(None, None),
        q12_instructed_share_same_periods=None,
    ),
    (1, Q26_WITH_ABSVD, "other", None): dict(unit_periods=6, value="0.857143"),
    (1, Q26_WITH_ABSVD, "wind", None): dict(unit_periods=2, value="1.000000"),
    (1, Q26_WITH_ABSVD, "gas", None): dict(unit_periods=0, value="0.000000"),
    (1, Q26_WITH_ABSVD, "hydro", None): dict(unit_periods=0, value=None),
    (1, Q26_UNCAPTURED, "other", None): dict(unit_periods=1),
    (1, Q26_UNCAPTURED, "wind", None): dict(unit_periods=0),
    (1, Q26_UNCAPTURED, "gas", None): dict(unit_periods=0),
    (1, Q26_UNCAPTURED, "hydro", None): dict(unit_periods=0),
    (1, Q26_YOUNG, "other", None): dict(unit_periods=1),
    (1, Q26_YOUNG, "wind", None): dict(unit_periods=0),
    (1, Q26_YOUNG, "gas", None): dict(unit_periods=0),
    (1, Q26_YOUNG, "hydro", None): dict(unit_periods=1),
    (2, Q26_FALL, "other", None): q26_figures(
        7, before="44.0", after="29.0", fall="15.0", fall_share="0.340909"
    ),
    (2, Q26_FALL, "wind", None): q26_figures(
        2, before="11.0", after="12.0", fall="-1.0", fall_share="-0.090909"
    ),
    (2, Q26_FALL, "gas", None): q26_figures(
        2, before="2.0", after="2.0", fall="0.0", fall_share="0.000000"
    ),
    (2, Q26_FALL, "hydro", None): q26_figures(
        0, before="0.0", after="0.0", fall="0.0", detail=Q26_NOT_APPLICABLE
    ),
    # Other, October: I 3, A 11, U 28, U' 17; March: I 4, A 6, U 16, U' 12.
    (3, Q26_MONTH, "other", OCTOBER_2025): dict(
        **q26_figures(4, "3.0", "11.0", "28.0", "17.0", "0.451613", "11.0", "0.392857"),
        **q26_dates("2025-10-25", "2025-10-26"),
    ),
    (3, Q26_MONTH, "other", MARCH_2026): dict(
        **q26_figures(3, "4.0", "6.0", "16.0", "12.0", "0.454545", "4.0", "0.250000"),
        **q26_dates("2026-03-29", "2026-03-30"),
    ),
    (3, Q26_MONTH, "wind", OCTOBER_2025): dict(
        **q26_figures(1, "4.0", "2.0", "6.0", "8.0", "0.428571", "-2.0", "-0.333333"),
        **q26_dates("2025-10-26", "2025-10-26"),
    ),
    (3, Q26_MONTH, "wind", MARCH_2026): dict(
        **q26_figures(1, "2.0", "1.0", "5.0", "4.0", "0.428571", "1.0", "0.200000"),
        **q26_dates("2026-03-30", "2026-03-30"),
    ),
    (3, Q26_MONTH, "gas", OCTOBER_2025): dict(
        **q26_figures(
            1, "0.0", "0.0", "0.0", "0.0", None, "0.0", None, Q26_NOT_APPLICABLE
        ),
        **q26_dates("2025-10-26", "2025-10-26"),
    ),
    (3, Q26_MONTH, "gas", MARCH_2026): dict(
        **q26_figures(1, "6.0", "0.0", "2.0", "2.0", "0.750000", "0.0", "0.000000"),
        **q26_dates("2026-03-30", "2026-03-30"),
    ),
    (4, Q26_PLACEBO, "other", None): q26_figures(
        2,
        "1.0",
        "7.0",
        "17.0",
        "10.0",
        "0.444444",
        "7.0",
        "0.411765",
        Q26_PLACEBO_DETAIL,
    ),
    (4, Q26_PLACEBO, "wind", None): q26_figures(
        2,
        "6.0",
        "7.0",
        "11.0",
        "10.0",
        "0.565217",
        "1.0",
        "0.090909",
        Q26_PLACEBO_DETAIL,
    ),
    (4, Q26_PLACEBO, "gas", None): q26_figures(
        2, "6.0", "0.0", "2.0", "2.0", "0.750000", "0.0", "0.000000", Q26_PLACEBO_DETAIL
    ),
    (4, Q26_PLACEBO, "hydro", None): q26_figures(
        0,
        "0.0",
        "0.0",
        "0.0",
        "0.0",
        None,
        "0.0",
        None,
        f"{Q26_NOT_APPLICABLE}; {Q26_PLACEBO_DETAIL}",
    ),
    (4, Q26_PLACEBO, Q26_ALL, None): q26_figures(
        6,
        "13.0",
        "14.0",
        "30.0",
        "22.0",
        "0.551020",
        "8.0",
        "0.266667",
        Q26_PLACEBO_DETAIL,
    ),
    (4, Q26_REAL_ON_PLACEBO, "other", None): q26_figures(
        2, "1.0", "8.0", "17.0", "9.0", "0.500000", "8.0", "0.470588"
    ),
    (4, Q26_REAL_ON_PLACEBO, "wind", None): q26_figures(
        2, "6.0", "3.0", "11.0", "12.0", "0.428571", "-1.0", "-0.090909"
    ),
    (4, Q26_REAL_ON_PLACEBO, "gas", None): q26_figures(
        2, "6.0", "0.0", "2.0", "2.0", "0.750000", "0.0", "0.000000"
    ),
    (4, Q26_REAL_ON_PLACEBO, "hydro", None): q26_figures(
        0, "0.0", "0.0", "0.0", "0.0", None, "0.0", None, Q26_NOT_APPLICABLE
    ),
    (4, Q26_REAL_ON_PLACEBO, Q26_ALL, None): q26_figures(
        6, "13.0", "11.0", "30.0", "23.0", "0.510638", "7.0", "0.233333"
    ),
    (4, Q26_NO_PARTNER, "other", None): dict(unit_periods=3),
    (4, Q26_NO_PARTNER, "wind", None): dict(unit_periods=0),
    (4, Q26_NO_PARTNER, "gas", None): dict(unit_periods=0),
    (4, Q26_NO_PARTNER, "hydro", None): dict(unit_periods=0),
    (4, Q26_NO_PARTNER, Q26_ALL, None): dict(unit_periods=3),
    (4, Q26_PARTNER_UNCAPTURED, "other", None): dict(unit_periods=1),
    (4, Q26_PARTNER_UNCAPTURED, "wind", None): dict(unit_periods=0),
    (4, Q26_PARTNER_UNCAPTURED, "gas", None): dict(unit_periods=0),
    (4, Q26_PARTNER_UNCAPTURED, "hydro", None): dict(unit_periods=0),
    (4, Q26_PARTNER_UNCAPTURED, Q26_ALL, None): dict(unit_periods=1),
    (4, Q26_PARTNER_YOUNG, "other", None): dict(unit_periods=1),
    (4, Q26_PARTNER_YOUNG, "wind", None): dict(unit_periods=0),
    (4, Q26_PARTNER_YOUNG, "gas", None): dict(unit_periods=0),
    (4, Q26_PARTNER_YOUNG, "hydro", None): dict(unit_periods=0),
    (4, Q26_PARTNER_YOUNG, Q26_ALL, None): dict(unit_periods=1),
    # Captured up to the cutoff: 23, 25, 26 October, 28, 29, 30 March; 23 October
    # and 28 March are under 35 days (B1's and H1's rows on 28 March).
    (5, Q26_CAPTURED_DATES, None, None): dict(
        settlement_dates=6, **q26_dates("2025-10-23", "2026-03-30")
    ),
    (5, Q26_COUNTED_DATES, None, None): dict(
        settlement_dates=4,
        **q26_dates("2025-10-25", "2026-03-30"),
        detail=Q26_LATEST_CAPTURE,
    ),
    (5, Q26_YOUNG_DATES, None, None): dict(
        settlement_dates=2, unit_periods=2, **q26_dates("2025-10-23", "2026-03-28")
    ),
    (5, Q26_UNCAPTURED, None, None): dict(unit_periods=1),
    # Window from 25 October 2025 (the first counted date). Idle: G1 on
    # 26 October p48 of the result's 11 unit-periods (B1's p50, PN 0 and
    # metered -4, is not idle).
    (6, Q26_ACTIVE_UNITS, None, None): dict(value="4", first_date="2025-10-25"),
    (6, Q26_INTERCONNECTOR_UNITS, None, None): dict(value="2"),
    (6, Q26_INTELE_UNITS, None, None): dict(value="1"),
    (6, Q26_INACTIVE_UNITS, None, None): dict(value="2"),
    (6, Q26_INTERCONNECTOR_PERIODS, None, None): dict(unit_periods=2),
    (6, Q26_INACTIVE_PERIODS, None, None): dict(unit_periods=2),
    (6, Q26_IDLE, None, None): dict(unit_periods=1, value="0.090909"),
}


def q26_mismatches(conn, reached, time_zone):
    title = f"[{time_zone}] Q26"
    query = on_fixtures(reached, Q26, Q26_TABLES)
    run = run_on_fixtures(conn, Q26_FIXTURES, query, time_zone, key_columns=Q26_KEY)
    found = mismatches(title, run, Q26_EXPECTED)
    found += unexpected_rows(title, run, Q26_EXPECTED)
    second_run = run_on_fixtures(
        conn, Q26_FIXTURES, query, time_zone, key_columns=Q26_KEY
    )
    if second_run != run:
        found.append(f"{title}: a second run gives different rows")
    return found


def check_all(
    reached,
    not_reached,
    boundary,
    project_values,
    schedule_window,
    q4_other_cutoffs,
):
    """Every mismatch between the analyses on the fixtures and the worked values.

    reached, not_reached, boundary, project_values and schedule_window are
    manifests compiled with REACHED_VARS, NOT_REACHED_VARS, boundary_vars(),
    PROJECT_VALUES_VARS and Q27_SCHEDULE_VARS; q4_other_cutoffs holds the Q4
    manifests of Q4_OTHER_CUTOFF_VARS by name. Each case runs in a UTC
    session and in a Europe/London one.
    """

    conn = disposable_connection()
    found = known_pick_mismatches()
    try:
        for time_zone in SESSION_TIME_ZONES:
            found += q1_verdict_mismatches(conn, project_values, time_zone)
            found += q1_beside_mismatches(conn, project_values, time_zone)
            found += q10_mismatches(conn, reached, not_reached, time_zone)
            found += q10_project_values_mismatches(conn, project_values, time_zone)
            found += q4_mismatches(conn, reached, not_reached, boundary, time_zone)
            found += q4_other_cutoff_mismatches(conn, q4_other_cutoffs, time_zone)
            found += q2rf_mismatches(
                conn, reached, not_reached, project_values, time_zone
            )
            found += q25_mismatches(conn, reached, time_zone)
            found += q27_mismatches(conn, reached, not_reached, time_zone)
            found += q27_schedule_change_mismatches(conn, schedule_window, time_zone)
            found += q27_project_values_mismatches(conn, project_values, time_zone)
            found += q8_q28_mismatches(conn, reached, boundary, time_zone)
            found += q29_mismatches(conn, reached, time_zone)
            found += q30_mismatches(conn, reached, boundary, time_zone)
            found += q26_mismatches(conn, reached, time_zone)
    finally:
        conn.close()
    return found


def boundary_vars():
    """REACHED_VARS with Q4's, the Q8 and Q28 and Q30's usable-draw minimums
    at their fixtures' usable counts."""

    variables = dict(REACHED_VARS)
    variables["shortfall_error_bootstrap_min_usable_draws"] = (
        usable_draws_without_b_twice()
    )
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
            "One-look analysis checks require opt-in to a local disposable gridskew_dev"
        )
    found = check_all(
        compile_analyses(REACHED_VARS, also_compiled=(WIND_ERROR_MODEL,)),
        compile_analyses(NOT_REACHED_VARS),
        compile_analyses(boundary_vars()),
        compile_analyses(PROJECT_VALUES_VARS),
        compile_analyses(Q27_SCHEDULE_VARS),
        compile_q4_other_cutoffs(),
    )
    for mismatch in found:
        print(mismatch)
    if found:
        raise SystemExit(
            f"{len(found)} one-look analysis cells differ from the worked values"
        )
    print(
        "One-look analyses: Q1, Q10, Q4, Q2-RF, Q25, Q27, Q8, Q28, Q29, Q30 and "
        "Q26 match every worked value"
    )


if __name__ == "__main__":
    main()
