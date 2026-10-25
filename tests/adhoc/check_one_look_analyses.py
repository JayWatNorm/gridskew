"""Check the Q10, Q4, Q2-RF, Q25 and Q27 one-look analyses on hand-worked rows.

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
REACHED_VARS = {
    "analysis_read_until": READ_UNTIL,
    "forecast_drift_half_hours_needed": 7,
    "shortfall_error_matched_days_needed": 2,
    "rf_restatement_dates_needed": 2,
    "forecast_stalls_days_needed": 4,
    **STALLS_WINDOW_VARS,
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

Q10_FIXTURES = f"""
create temporary table synthetic_carbon_error (
    period_start timestamptz,
    has_complete_trajectory boolean,
    error_24h numeric,
    error_4h numeric,
    error_final numeric
) on commit drop;
insert into synthetic_carbon_error values {Q10_ROWS};
"""

# Q4's days, settlement dates in summer time: A 1 September and B 2 September
# are matched (48 and 46 half-hours with both values); C 3 September has 45
# (not matched); E 6 September is after analysis_read_until. Every half-hour
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
    error_final numeric
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
        when days.settlement_date = date '2026-09-01' and periods.period <= 10 then {a_bottom}
        when days.settlement_date = date '2026-09-02' and periods.period <= 9 then {b_bottom}
        when days.settlement_date = date '2026-09-02' and periods.period = 10 then {b_tenth}
        when days.settlement_date = date '2026-09-02' and periods.period >= 47 then null
        when days.settlement_date = date '2026-09-03' and periods.period >= 46 then null
        when days.settlement_date in (date '2026-09-01', date '2026-09-02') and periods.period >= 39 then 1.25
        when days.settlement_date in (date '2026-09-01', date '2026-09-02') then 1
        else 50
    end as cohort_measure
from (
    values (date '2026-09-01'), (date '2026-09-02'), (date '2026-09-03'), (date '2026-09-06')
) as days (settlement_date)
cross join generate_series(1, 48) as periods (period);

insert into synthetic_carbon_error
select
    period_start,
    case when period % 2 = 1 then period else -period end
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
"""

Q10_TABLES = {"int_carbon_error_by_period": "pg_temp.synthetic_carbon_error"}
Q4_TABLES = {
    "int_carbon_error_by_period": "pg_temp.synthetic_carbon_error",
    "int_shortfall_by_unit_period": "pg_temp.synthetic_shortfall",
    "int_elexon__bm_unit_cohort": "pg_temp.synthetic_cohort",
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
BOTTOM = (1, "bottom quintile mean, MWh", None)
TOP = (1, "top quintile mean, MWh", None)
DIFFERENCE = (1, "difference, top minus bottom, MWh", None)
RATIO = (1, "ratio, top over bottom", None)
VERDICT = (1, "verdict (supported at a ratio of 1.25 or more)", None)
DRAWS = (1, "bootstrap draws with a ratio (900 of 1000 needed)", None)
LOW = (1, "bootstrap 90% interval, low", None)
HIGH = (1, "bootstrap 90% interval, high", None)
QUINTILE = "quintile of abs(error_final): mean positive shortfall, MWh"

# Q4 with A's and B's first ten half-hours at 1 except B10 at 3; 11 to 38 at
# 1; 39 onwards at 1.25. Quintile 1: 19 at 1. Quintile 2 (positions 20 to 38):
# B10 at 3 and 18 at 1, 21 / 19. Quintile 5: 18 at 1.25. Ratio 1.25 exactly:
# supported at the boundary.
#
# Bootstrap: a draw picks two days. A and B, either order, is the observed
# sample (ties still break A first): 1.25. A twice: quintile 1 is A1 to A10
# twice (1), quintile 5 is the second A39 and A40 to A48 twice (1.25): 1.25.
# B twice (92 rows: 19, 19, 18, 18, 18): quintile 1 is B1 to B9 twice and one
# B10, 21 / 19; quintile 5 is B38 twice and B39 to B46 twice, 22 / 18; ratio
# 418 / 378 = 1.105820. B-twice draws are about a quarter, so the 5th
# percentile is 1.105820 and the 95th 1.25.
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
    LOW: dict(value="1.105820"),
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
    (3, "settlement dates with an error_final", None): dict(
        days=3, first_date="2026-09-01", last_date="2026-09-03"
    ),
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

# B's first ten half-hours at 0: the observed bottom mean is A's ten 1s over
# 19, 10 / 19, and the ratio 1.25 x 19 / 10 = 2.375. Every B-twice draw has a
# bottom mean of 0 and forms no ratio, so fewer than 900 draws are usable.
Q4_UNAVAILABLE_EXPECTED = {
    BOTTOM: dict(value="0.526316"),
    RATIO: dict(value="2.375000"),
    VERDICT: dict(result="supported"),
    LOW: dict(value=None),
    HIGH: dict(value=None),
}

# The same rows with the usable-draw minimum set to exactly the usable count:
# the interval is available at its boundary. Its draws are A twice (1.25)
# and A with B (2.375), each well over 5% of them.
Q4_BOUNDARY_EXPECTED = {
    LOW: dict(value="1.250000"),
    HIGH: dict(value="2.375000"),
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


# Q25: one acceptance that crosses the London midnight at 23:00 UTC on 31
# August. The first day's capture holds its three ramp points, 22:50 to
# 23:05; the second day's capture holds only the point from midnight. Taking
# the latest capture of each ramp point keeps all three: a 15-minute span
# whose first settlement date, 31 August, puts it in August. Taking the
# latest capture of the whole acceptance would keep the last point alone: 5
# minutes, under 10, in September.
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
    ('STOR-1', 7, '2026-08-31 22:45+00', '2026-08-31',
        '2026-08-31 22:50+00', '2026-08-31 22:55+00', '2026-09-01 01:00+00'),
    ('STOR-1', 7, '2026-08-31 22:45+00', '2026-08-31',
        '2026-08-31 22:55+00', '2026-08-31 23:00+00', '2026-09-01 01:00+00'),
    ('STOR-1', 7, '2026-08-31 22:45+00', '2026-08-31',
        '2026-08-31 23:00+00', '2026-08-31 23:05+00', '2026-09-01 01:00+00'),
    ('STOR-1', 7, '2026-08-31 22:45+00', '2026-09-01',
        '2026-08-31 23:00+00', '2026-08-31 23:05+00', '2026-09-02 01:00+00');
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
    (1, "other", "2026-08-01", GRANULARITY): dict(
        n=1, under_10_minutes=0, median_minutes="15.0"
    ),
    (2, "other", None, GRANULARITY): dict(
        n=1, under_10_minutes=0, median_minutes="15.0"
    ),
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


SESSION_TIME_ZONES = ("UTC", "Europe/London")


def q4_fixtures(a_bottom, b_bottom, b_tenth):
    return Q4_FIXTURES.format(a_bottom=a_bottom, b_bottom=b_bottom, b_tenth=b_tenth)


def q2rf_fixtures(extra_cohort_rows=""):
    return Q2RF_FIXTURES.format(extra_cohort_rows=extra_cohort_rows)


def usable_draws_without_b_twice():
    return BOOTSTRAP_DRAWS - draws_without_day_one(2)


def picked_day(draw, pick, days):
    """The day a bootstrap pick takes, worked out from the contract's rule."""

    digest = hashlib.md5(f"{BOOTSTRAP_SEED}:{draw}:{pick}".encode()).hexdigest()
    return 1 + int(digest[:8], 16) % days


def draws_without_day_one(days):
    """How many draws pick only days other than the first."""

    count = 0
    for draw in range(1, BOOTSTRAP_DRAWS + 1):
        picks = [picked_day(draw, pick, days) for pick in range(1, days + 1)]
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
    main_run = run_analysis(conn, Q10_FIXTURES, reached, Q10, Q10_TABLES, time_zone)
    early_run = run_analysis(
        conn, Q10_FIXTURES, not_reached, Q10, Q10_TABLES, time_zone
    )
    found = mismatches(title, main_run, Q10_EXPECTED)
    found += unexpected_rows(title, main_run, Q10_EXPECTED)
    found += mismatches(f"{title} early", early_run, Q10_NOT_REACHED_EXPECTED)
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
    second_run = run_analysis(
        conn, q4_fixtures(1, 1, 3), reached, Q4, Q4_TABLES, time_zone
    )
    if second_run != main_run:
        found.append(f"{title}: a second run gives different rows")

    unavailable_expected = dict(Q4_UNAVAILABLE_EXPECTED)
    unavailable_expected[DRAWS] = dict(value=str(usable_draws), result="unavailable")
    unavailable_run = run_analysis(
        conn, q4_fixtures(1, 0, 0), reached, Q4, Q4_TABLES, time_zone
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
        conn, q4_fixtures(1, 0, 0), boundary, Q4, Q4_TABLES, time_zone
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
        f"{title} two groups", two_groups_run, {REPEATED_ID: dict(units=1)}
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
    finally:
        conn.close()
    return found


def boundary_vars():
    """REACHED_VARS with Q4's usable-draw minimum at the fixture's usable count."""

    variables = dict(REACHED_VARS)
    variables["shortfall_error_bootstrap_min_usable_draws"] = (
        usable_draws_without_b_twice()
    )
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
    print("One-look analyses: Q10, Q4, Q2-RF, Q25 and Q27 match every worked value")


if __name__ == "__main__":
    main()
