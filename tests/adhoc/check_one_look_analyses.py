"""Check the one-look analyses (Q10, Q4, Q2-RF, Q25, Q27, Q8, Q28, Q29) on worked rows.

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
import os
from datetime import date, datetime, timezone
from decimal import Decimal

import psycopg

from tests.adhoc.check_analyses_compile import compile_analyses, disposable_connection

Q10 = "q10_forecast_accuracy_by_lead_time"
Q4 = "q4_forecast_error_vs_shortfall"
Q2RF = "q2rf_restatement_r3_to_rf"
Q25 = "q25_instruction_fragmentation"
Q27 = "q27_forecast_stalls"
READ_UNTIL = "2026-09-05 00:00:00+00"
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
REACHED_VARS = {
    "analysis_read_until": READ_UNTIL,
    "forecast_drift_half_hours_needed": 7,
    "shortfall_error_matched_days_needed": 2,
    "rf_restatement_dates_needed": 2,
    "forecast_stalls_days_needed": 4,
    **STALLS_WINDOW_VARS,
    **Q29_WINDOW_VARS,
}
NOT_REACHED_VARS = {
    "analysis_read_until": READ_UNTIL,
    "forecast_drift_half_hours_needed": 8,
    "shortfall_error_matched_days_needed": 3,
    "rf_restatement_dates_needed": 4,
    "forecast_stalls_days_needed": 5,
    **STALLS_WINDOW_VARS,
}
# The bootstrap's fixed seed and draw count, as in the Q4 analysis.
BOOTSTRAP_SEED = 20261012
BOOTSTRAP_DRAWS = 1000

# Q10's rows: (period_start UTC, complete trajectory, error_24h, error_4h, error_final).
# Local (Europe/London) dates in summer time are an hour ahead of UTC.
Q10_ROWS = """
    ('2026-08-28 10:00+00', true, 10, -4, 2),       -- Friday: weekday
    ('2026-08-30 23:30+00', true, -6, 3, -1),       -- Monday 31 August locally: bank holiday (Sunday in UTC)
    ('2026-08-29 12:00+00', true, 4, 4, 4),         -- Saturday: weekend
    ('2026-08-28 23:30+00', true, 2, -2, 0),        -- Saturday locally: weekend (Friday in UTC)
    ('2026-08-28 11:00+00', true, null, 5, 1),      -- no 24-hour forecast: dropped
    ('2026-08-28 12:00+00', true, 3, null, null),   -- no 4-hour or final forecast: dropped
    ('2026-08-28 13:00+00', false, 100, 100, 100),  -- incomplete trajectory: not in Q1's population
    ('2026-08-16 12:00+00', true, 100, 100, 100),   -- before the population starts
    ('2026-09-05 00:30+00', true, 100, 100, 100),   -- after analysis_read_until
    ('2026-09-05 00:00+00', true, 0, 8, -3)         -- at analysis_read_until; Saturday: weekend
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

Q10_FIXTURES = f"""
create temporary table synthetic_carbon_error (
    period_start timestamptz,
    has_complete_trajectory boolean,
    error_24h numeric,
    error_4h numeric,
    error_final numeric
) on commit drop;
insert into synthetic_carbon_error values {Q10_ROWS};
create temporary table synthetic_slot_polls (
    poll_slot timestamptz,
    is_first_poll_in_slot boolean
) on commit drop;
insert into synthetic_slot_polls values {{look_ahead_polls}};
"""


def q10_fixtures(third_poll=THIRD_POLL_AFTER_CUTOFF):
    polls = Q10_LOOK_AHEAD_POLLS.format(third_poll=third_poll)
    return Q10_FIXTURES.format(look_ahead_polls=polls)


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
    determinability text
) on commit drop;
create temporary table synthetic_cohort (national_grid_bm_unit text) on commit drop;

insert into synthetic_cohort values ('U1'), ('U2'), ('U4'), ('U5');

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
    case when period % 2 = 1 then period else -period end,
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
select 'U1', date '2026-09-01', timestamptz '2026-09-01 12:15:00+00', 5, 'determinable';

create temporary table synthetic_slot_polls (
    poll_slot timestamptz,
    is_first_poll_in_slot boolean
) on commit drop;
insert into synthetic_slot_polls values
    ('2026-09-05 00:30+00', true), ('2026-09-05 01:00+00', true), ('2026-09-05 01:30+00', true);
"""

Q10_TABLES = {
    "int_carbon_error_by_period": "pg_temp.synthetic_carbon_error",
    "int_carbon_forecast__revisions": "pg_temp.synthetic_slot_polls",
}
Q4_TABLES = {
    "int_carbon_error_by_period": "pg_temp.synthetic_carbon_error",
    "int_shortfall_by_unit_period": "pg_temp.synthetic_shortfall",
    "int_elexon__bm_unit_cohort": "pg_temp.synthetic_cohort",
    "int_carbon_forecast__revisions": "pg_temp.synthetic_slot_polls",
}

FIRST = "2026-08-28 10:00:00+00:00"
LAST = "2026-09-05 00:00:00+00:00"
Q1_POPULATION = (1, "Q1 one-look half-hours (7 needed)", None)
ACCURACY = "accuracy on the common sample"

# (section, item, horizon) -> {column: expected}. The common sample is the
# five rows with all three errors: 24 hours 10, -6, 4, 2, 0; 4 hours
# -4, 3, 4, -2, 8; final 2, -1, 4, 0, -3.
Q10_EXPECTED = {
    Q1_POPULATION: dict(
        half_hours=7,
        first_half_hour=FIRST,
        last_half_hour=LAST,
        population_is_reached=True,
    ),
    (1, "common sample: all three horizons", None): dict(
        half_hours=5, first_half_hour=FIRST, last_half_hour=LAST
    ),
    (1, "with a forecast at this horizon", "24 hours"): dict(half_hours=6),
    (1, "with a forecast at this horizon", "4 hours"): dict(half_hours=6),
    (1, "with a forecast at this horizon", "final"): dict(half_hours=6),
    (1, "dropped: no forecast at this horizon", "24 hours"): dict(
        half_hours=1,
        first_half_hour="2026-08-28 11:00:00+00:00",
        last_half_hour="2026-08-28 11:00:00+00:00",
    ),
    (1, "dropped: no forecast at this horizon", "4 hours"): dict(
        half_hours=1, first_half_hour="2026-08-28 12:00:00+00:00"
    ),
    (1, "dropped: no forecast at this horizon", "final"): dict(
        half_hours=1, first_half_hour="2026-08-28 12:00:00+00:00"
    ),
    # 24 hours: 22 / 5 and 10 / 5; 4 hours: 21 / 5 and 9 / 5; final: 10 / 5 and 2 / 5.
    (2, ACCURACY, "24 hours"): dict(
        half_hours=5, mean_absolute_error="4.400", bias="2.000"
    ),
    (2, ACCURACY, "4 hours"): dict(
        half_hours=5, mean_absolute_error="4.200", bias="1.800"
    ),
    (2, ACCURACY, "final"): dict(
        half_hours=5, mean_absolute_error="2.000", bias="0.400"
    ),
    # Weekday: the Friday 10:00 row only.
    (3, "settlement date: weekday", "24 hours"): dict(
        half_hours=1, mean_absolute_error="10.000", bias="10.000"
    ),
    (3, "settlement date: weekday", "4 hours"): dict(
        half_hours=1, mean_absolute_error="4.000", bias="-4.000"
    ),
    (3, "settlement date: weekday", "final"): dict(
        half_hours=1, mean_absolute_error="2.000", bias="2.000"
    ),
    # Weekend: 24 hours 4, 2, 0; 4 hours 4, -2, 8 (14 / 3, 10 / 3); final 4, 0, -3 (7 / 3, 1 / 3).
    (3, "settlement date: weekend", "24 hours"): dict(
        half_hours=3, mean_absolute_error="2.000", bias="2.000"
    ),
    (3, "settlement date: weekend", "4 hours"): dict(
        half_hours=3, mean_absolute_error="4.667", bias="3.333"
    ),
    (3, "settlement date: weekend", "final"): dict(
        half_hours=3, mean_absolute_error="2.333", bias="0.333"
    ),
    (3, "settlement date: bank holiday", "24 hours"): dict(
        half_hours=1, mean_absolute_error="6.000", bias="-6.000"
    ),
    (3, "settlement date: bank holiday", "4 hours"): dict(
        half_hours=1, mean_absolute_error="3.000", bias="3.000"
    ),
    (3, "settlement date: bank holiday", "final"): dict(
        half_hours=1, mean_absolute_error="1.000", bias="-1.000"
    ),
}

Q10_NOT_REACHED_EXPECTED = {
    (1, "Q1 one-look half-hours (8 needed)", None): dict(
        half_hours=7, population_is_reached=False
    ),
    (2, ACCURACY, "24 hours"): dict(half_hours=5, mean_absolute_error=None, bias=None),
    (3, "settlement date: weekend", "4 hours"): dict(
        half_hours=3, mean_absolute_error=None, bias=None
    ),
}

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
Q4_EXPECTED = {
    POPULATION: dict(
        half_hours=94,
        days=2,
        first_date="2026-09-01",
        last_date="2026-09-02",
        result="reached",
    ),
    BOTTOM: dict(value="1.000000"),
    TOP: dict(value="1.250000"),
    DIFFERENCE: dict(value="0.250000"),
    RATIO: dict(value="1.250000"),
    VERDICT: dict(result="supported"),
    DRAWS: dict(value="1000", result="available"),
    LOW: dict(value="1.041667"),
    HIGH: dict(value="1.250000"),
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
}

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
}

# Before its population each analysis shows counts only: these columns are
# null in every row of these sections.
Q10_GATED = ({2, 3}, ("mean_absolute_error", "bias"))
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


# Q2-RF's B1610 rows. 22 and 23 August 2025 are the first two dates with
# both runs; 20 August has only R3 and 21 August only RF (neither is a
# population date); 24 August is a third date (outside the first two);
# 6 September 2026 is after analysis_read_until. T_ZERO's R3 is 0, so its
# group's share is not defined; T_SOLAR has only an R3 row, so its group has
# no unit-period in both runs. extra_cohort_rows lets one case give T_GAS-1
# a second fuel group.
Q2RF_FIXTURES = """
create temporary table synthetic_b1610 (
    bm_unit text,
    settlement_date date,
    settlement_period integer,
    settlement_run_type text,
    quantity numeric
) on commit drop;
create temporary table synthetic_cohort (
    national_grid_bm_unit text,
    elexon_bm_unit text,
    fuel_group text
) on commit drop;

insert into synthetic_cohort values
    ('GAS-1', 'T_GAS-1', 'gas'),
    ('GAS-1A', 'T_GAS-1', 'gas'),   -- the same Elexon ID twice, same group
    ('WIND-1', 'T_WIND-1', 'wind'),
    ('ZERO-1', 'T_ZERO', 'hydro'),
    ('SOLAR-1', 'T_SOLAR', 'solar'),
    ('NOID-1', null, 'gas'){extra_cohort_rows};

insert into synthetic_b1610 values
    ('T_GAS-1', '2025-08-20', 1, 'R3', 70),
    ('T_GAS-1', '2025-08-21', 1, 'RF', 50),
    ('T_GAS-1', '2025-08-22', 1, 'R3', 10), ('T_GAS-1', '2025-08-22', 1, 'RF', 12),
    ('T_GAS-1', '2025-08-22', 1, 'SF', 999),
    ('T_WIND-1', '2025-08-22', 1, 'R3', -4), ('T_WIND-1', '2025-08-22', 1, 'RF', -1),
    ('2__SUPP', '2025-08-22', 1, 'R3', 5), ('2__SUPP', '2025-08-22', 1, 'RF', 5),
    ('T_ZERO', '2025-08-22', 1, 'R3', 0), ('T_ZERO', '2025-08-22', 1, 'RF', 3),
    ('T_SOLAR', '2025-08-22', 1, 'R3', 9),
    ('T_GAS-1', '2025-08-22', 2, 'R3', 20), ('T_GAS-1', '2025-08-22', 2, 'RF', 15),
    ('T_WIND-1', '2025-08-22', 2, 'R3', 6),
    ('2__SUPP', '2025-08-22', 2, 'RF', 7),
    ('T_GAS-1', '2025-08-23', 1, 'R3', 8), ('T_GAS-1', '2025-08-23', 1, 'RF', 8),
    ('2__SUPP', '2025-08-23', 1, 'R3', -10), ('2__SUPP', '2025-08-23', 1, 'RF', -6),
    ('T_GAS-1', '2025-08-24', 1, 'R3', 100), ('T_GAS-1', '2025-08-24', 1, 'RF', 0),
    ('T_GAS-1', '2026-09-06', 1, 'R3', 1000), ('T_GAS-1', '2026-09-06', 1, 'RF', 0);
"""

Q2RF_TABLES = {
    "elexon_b1610": "pg_temp.synthetic_b1610",
    "int_elexon__bm_unit_cohort": "pg_temp.synthetic_cohort",
}

# (section, item, None) -> {column: expected}. Gas: 10 to 12, 20 to 15, 8 to
# 8: gross 7 of 38, net -3. Wind: -4 to -1 (6 only in R3): 3 of 4, net 3.
# Outside: 5 to 5, -10 to -6 (7 only in RF): 4 of 15, net 4. Hydro: 0 to 3,
# not defined. Overall: 17 of 57, net 7.
Q2RF_EXPECTED = {
    (1, "gas", None): dict(
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
        dates=2,
        first_date="2025-08-22",
        last_date="2025-08-23",
    ),
    (1, "wind", None): dict(
        unit_periods=1,
        gross_change_mwh="3.000",
        r3_absolute_mwh="4.000",
        gross_share="0.750000",
        net_share="0.750000",
        only_in_r3=1,
        only_in_rf=0,
    ),
    (1, "outside the cohort", None): dict(
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
        unit_periods=1,
        gross_change_mwh="3.000",
        r3_absolute_mwh="0.000",
        gross_share=None,
        net_share=None,
        share_status="not defined: sum of abs(R3) is 0",
    ),
    (1, "overall", None): dict(
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
    (2, "date 2025-08-22: R3", None): dict(unit_periods=7, units=5, periods=2),
    (2, "date 2025-08-22: RF", None): dict(unit_periods=6, units=4, periods=2),
    (2, "date 2025-08-23: R3", None): dict(unit_periods=2, units=2, periods=1),
    (2, "date 2025-08-23: RF", None): dict(unit_periods=2, units=2, periods=1),
    (
        3,
        "Elexon IDs with more than one cohort fuel group (each would count twice)",
        None,
    ): dict(units=0),
}

REPEATED_ID = (
    3,
    "Elexon IDs with more than one cohort fuel group (each would count twice)",
    None,
)
TWO_GROUPS_FOR_ONE_ID = ",\n    ('GAS-1B', 'T_GAS-1', 'wind')"
# With T_GAS-1 in gas and wind, its three unit-periods (10 to 12, 20 to 15,
# 8 to 8) still count once overall: the main run's 7 unit-periods, 17 of 57,
# net 7. Counted once per group they would give 10 unit-periods, 24 of 95,
# net 4.
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

# Four dates needed: 22 to 24 August are before the cutoff, so three.
Q2RF_NOT_REACHED_EXPECTED = {
    (1, "overall", None): dict(
        unit_periods=8,
        gross_change_mwh=None,
        gross_share=None,
        share_status="population not reached: no result",
        dates=3,
    ),
}


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
Q27_FIXTURES = """
create temporary table synthetic_revisions (
    poll_slot timestamptz,
    is_first_poll_in_slot boolean,
    is_partial_poll boolean,
    is_no_change_poll boolean
) on commit drop;
insert into synthetic_revisions values
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
EXPLORATORY = "exploratory, seen before registration: "
BLOCK = "stalls starting {} UTC (split informed by the exploratory stalls)"


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
}
Q27_EXPECTED = {
    **Q27_WINDOW_EXPECTED,
    (1, "days covered (4 needed)", None): dict(value="4"),
    (1, "slot polls stored after analysis_read_until (3 needed)", None): dict(
        detail="yes"
    ),
    (1, "read possible", None): dict(detail="yes"),
    (2, "stalls", None): dict(value="3"),
    (2, "median duration, poll-equivalent minutes", None): dict(value="90"),
    (2, "longest duration, poll-equivalent minutes", None): dict(value="120"),
    (2, "median duration, elapsed minutes", None): dict(value="90"),
    (2, "longest duration, elapsed minutes", None): dict(value="150"),
    (2, "share of slot polls inside a stall", None): dict(value="0.750000"),
    (2, "stalls cut at the window start", None): dict(value="1"),
    (2, "stalls cut at the window end", None): dict(value="1"),
    (3, "partial polls", None): dict(value="2"),
    (3, "share of slot polls that are partial", None): dict(value="0.166667"),
    (3, "partial polls inside a stall", None): dict(value="1"),
    (4, BLOCK.format("00:00-05:59"), None): dict(value="0"),
    (4, BLOCK.format("06:00-11:59"), None): dict(value="0"),
    (4, BLOCK.format("12:00-17:59"), None): dict(value="1"),
    (4, BLOCK.format("18:00-23:59"), None): dict(value="2"),
    **Q27_EXPLORATORY_EXPECTED,
}
Q27_GATED = ({2, 3, 4}, ("value",))
# Not reached on days (5 needed), and, with the look-ahead row removed, not
# reached on the look-ahead: either way only sections 1 and 5 have values.
Q27_NOT_REACHED_EXPECTED = {
    **Q27_WINDOW_EXPECTED,
    (1, "days covered (5 needed)", None): dict(value="4"),
    (1, "read possible", None): dict(detail="no"),
    **Q27_EXPLORATORY_EXPECTED,
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


SESSION_TIME_ZONES = ("UTC", "Europe/London")


def q4_fixtures(a_bottom, b_bottom, a_tenth, stale_b_half_hours=0, extra_days=""):
    return Q4_FIXTURES.format(
        a_bottom=a_bottom,
        b_bottom=b_bottom,
        a_tenth=a_tenth,
        stale_b_half_hours=stale_b_half_hours,
        extra_days=extra_days,
    )


# A matched day before A (31 August) whose every final forecast is stale:
# it counts towards the gate but holds no half-hour for the quintiles, so
# the bootstrap must resample only A and B and give the same interval.
ALL_STALE_DAY = ", (date '2026-08-31')"


def q2rf_fixtures(extra_cohort_rows=""):
    return Q2RF_FIXTURES.format(extra_cohort_rows=extra_cohort_rows)


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


def on_fixtures(manifest, analysis, tables):
    """The analysis's compiled SQL reading the temporary tables instead."""

    node = manifest["nodes"][f"analysis.gridskew.{analysis}"]
    sql = node["compiled_code"]
    for relation_id in node["depends_on"]["nodes"]:
        if relation_id.startswith("source."):
            relation = manifest["sources"][relation_id]
        else:
            relation = manifest["nodes"][relation_id]
        if relation["name"] not in tables:
            raise SystemExit(
                f"{analysis} reads {relation['name']}, which has no fixture"
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
            if cells[column] is not None:
                found.append(
                    f"{title}: {key} {column} is {cells[column]!r} before the population"
                )
    return found


def run_analysis(conn, fixtures, manifest, analysis, tables, time_zone):
    query = on_fixtures(manifest, analysis, tables)
    return run_on_fixtures(conn, fixtures, query, time_zone)


def q10_mismatches(conn, reached, not_reached, time_zone):
    title = f"[{time_zone}] Q10"
    main_run = run_analysis(conn, q10_fixtures(), reached, Q10, Q10_TABLES, time_zone)
    early_run = run_analysis(
        conn, q10_fixtures(), not_reached, Q10, Q10_TABLES, time_zone
    )
    found = mismatches(title, main_run, Q10_EXPECTED)
    found += unexpected_rows(title, main_run, Q10_EXPECTED)
    found += mismatches(f"{title} early", early_run, Q10_NOT_REACHED_EXPECTED)
    found += gated_values_left_null(f"{title} early", early_run, Q10_GATED)

    short_look_ahead_run = run_analysis(
        conn, q10_fixtures(third_poll=""), reached, Q10, Q10_TABLES, time_zone
    )
    found += gated_values_left_null(
        f"{title} two polls after the cutoff", short_look_ahead_run, Q10_GATED
    )
    return found


def q4_mismatches(conn, reached, not_reached, boundary, time_zone):
    title = f"[{time_zone}] Q4"
    usable_draws = usable_draws_without_b_twice()
    main_run = run_analysis(
        conn, q4_fixtures(1, 1, 3), reached, Q4, Q4_TABLES, time_zone
    )
    found = mismatches(title, main_run, Q4_EXPECTED)
    found += unexpected_rows(title, main_run, Q4_EXPECTED)
    second_run = run_analysis(
        conn, q4_fixtures(1, 1, 3), reached, Q4, Q4_TABLES, time_zone
    )
    if second_run != main_run:
        found.append(f"{title}: a second run gives different rows")

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
        {POPULATION: dict(days=2, half_hours=91), LEFT_OUT: dict(half_hours=4)},
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
        },
    )
    return found


def q2rf_mismatches(conn, reached, not_reached, time_zone):
    title = f"[{time_zone}] Q2-RF"
    main_run = run_analysis(
        conn, q2rf_fixtures(), reached, Q2RF, Q2RF_TABLES, time_zone
    )
    found = mismatches(title, main_run, Q2RF_EXPECTED)
    found += unexpected_rows(title, main_run, Q2RF_EXPECTED)

    early_run = run_analysis(
        conn, q2rf_fixtures(), not_reached, Q2RF, Q2RF_TABLES, time_zone
    )
    found += mismatches(f"{title} early", early_run, Q2RF_NOT_REACHED_EXPECTED)
    found += gated_values_left_null(f"{title} early", early_run, Q2RF_GATED)

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
        {REPEATED_ID: dict(units=1), Q2RF_OVERALL: Q2RF_TWO_GROUPS_OVERALL},
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


def check_all(reached, not_reached, boundary):
    """Every mismatch between the analyses on the fixtures and the worked values.

    reached, not_reached and boundary are manifests compiled with REACHED_VARS,
    NOT_REACHED_VARS and boundary_vars(). Each case runs in a UTC session and
    in a Europe/London one.
    """

    conn = disposable_connection()
    found = []
    try:
        for time_zone in SESSION_TIME_ZONES:
            found += q10_mismatches(conn, reached, not_reached, time_zone)
            found += q4_mismatches(conn, reached, not_reached, boundary, time_zone)
            found += q2rf_mismatches(conn, reached, not_reached, time_zone)
            found += q25_mismatches(conn, reached, time_zone)
            found += q27_mismatches(conn, reached, not_reached, time_zone)
            found += q8_q28_mismatches(conn, reached, boundary, time_zone)
            found += q29_mismatches(conn, reached, time_zone)
    finally:
        conn.close()
    return found


def boundary_vars():
    """REACHED_VARS with Q4's and the Q8 and Q28 usable-draw minimums at their
    fixtures' usable counts."""

    variables = dict(REACHED_VARS)
    variables["shortfall_error_bootstrap_min_usable_draws"] = (
        usable_draws_without_b_twice()
    )
    variables["demand_history_bootstrap_min_usable_draws"] = history_usable_draws()
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
        compile_analyses(REACHED_VARS),
        compile_analyses(NOT_REACHED_VARS),
        compile_analyses(boundary_vars()),
    )
    for mismatch in found:
        print(mismatch)
    if found:
        raise SystemExit(
            f"{len(found)} one-look analysis cells differ from the worked values"
        )
    print(
        "One-look analyses: Q10, Q4, Q2-RF, Q25, Q27, Q8, Q28 and Q29 match "
        "every worked value"
    )


if __name__ == "__main__":
    main()
