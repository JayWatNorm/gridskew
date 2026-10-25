{{ config(group='analysis') }}

-- Q2-RF: how much does metered output change from R3 to RF? Reading rule
-- (research questions, registered 12 October 2026, before any RF value was
-- compared): population: the first 30 settlement dates whose RF is stored
-- that also hold R3. Measures as GS-02: gross unit-level change
-- sum(abs(RF - R3)) / sum(abs(R3)) over unit-periods holding both, by fuel
-- group and overall, with the net change beside it; n, dates and coverage;
-- if sum(abs(R3)) is zero for a group or overall, the numerator, denominator
-- and n are reported and that ratio is marked not defined, never zero. Read
-- once, when 30 dates are stored.
--
-- A date holds a run when at least one row of it is stored; section 2 shows
-- each run's periods and units per date. R3 and RF are full-joined by unit,
-- date and period: unit-periods in only one run are coverage, not change.
-- The fuel group comes from today's registry by Elexon ID; B1610 units
-- outside the cohort (no published fuel type, or not in the registry) form
-- one group, and "overall" covers every unit. Until 30 dates exist it
-- returns counts and no energy.

{% set dates_needed = var('rf_restatement_dates_needed') %}
{% set read_until = analysis_read_until() %}

with runs as (
    select
        bm_unit,
        settlement_date,
        settlement_period,
        settlement_run_type,
        quantity
    from {{ source('elexon', 'elexon_b1610') }}
    where settlement_run_type in ('R3', 'RF')
{% if read_until is not none %}
        and settlement_date::timestamp at time zone 'Europe/London' <= timestamptz '{{ read_until }}'
{% endif %}
),

dates_with_both_runs as (
    select
        settlement_date,
        row_number() over (order by settlement_date) as date_order
    from runs
    group by settlement_date
    having count(*) filter (where settlement_run_type = 'R3') > 0
        and count(*) filter (where settlement_run_type = 'RF') > 0
),

population_dates as (
    select settlement_date
    from dates_with_both_runs
    where date_order <= {{ dates_needed }}
),

gate as (
    select
        count(*) as dates,
        min(settlement_date) as first_date,
        max(settlement_date) as last_date,
        count(*) >= {{ dates_needed }} as is_reached
    from population_dates
),

population_runs as (
    select
        runs.bm_unit,
        runs.settlement_date,
        runs.settlement_period,
        runs.settlement_run_type,
        runs.quantity
    from runs
    inner join population_dates
        on runs.settlement_date = population_dates.settlement_date
),

r3 as (
    select bm_unit, settlement_date, settlement_period, quantity
    from population_runs
    where settlement_run_type = 'R3'
),

rf as (
    select bm_unit, settlement_date, settlement_period, quantity
    from population_runs
    where settlement_run_type = 'RF'
),

unit_periods as (
    select
        coalesce(r3.bm_unit, rf.bm_unit) as bm_unit,
        r3.quantity as r3_mwh,
        rf.quantity as rf_mwh
    from r3
    full join rf
        on r3.bm_unit = rf.bm_unit
        and r3.settlement_date = rf.settlement_date
        and r3.settlement_period = rf.settlement_period
),

cohort_fuel_groups as (
    select distinct
        elexon_bm_unit,
        fuel_group
    from {{ ref('int_elexon__bm_unit_cohort') }}
    where elexon_bm_unit is not null
),

grouped as (
    select
        unit_periods.bm_unit,
        unit_periods.r3_mwh,
        unit_periods.rf_mwh,
        coalesce(cohort_fuel_groups.fuel_group, 'outside the cohort') as fuel_group
    from unit_periods
    left join cohort_fuel_groups
        on unit_periods.bm_unit = cohort_fuel_groups.elexon_bm_unit
),

measures as (
    select
        case when grouping(fuel_group) = 1 then 'overall' else fuel_group end as item,
        count(*) filter (where r3_mwh is not null and rf_mwh is not null) as unit_periods,
        count(distinct bm_unit) filter (where r3_mwh is not null and rf_mwh is not null) as units,
        count(*) filter (where rf_mwh <> r3_mwh) as changed_unit_periods,
        coalesce(sum(abs(rf_mwh - r3_mwh)), 0) as gross_change_mwh,
        coalesce(sum(abs(r3_mwh)) filter (where rf_mwh is not null), 0) as r3_absolute_mwh,
        coalesce(sum(rf_mwh - r3_mwh), 0) as net_change_mwh,
        count(*) filter (where rf_mwh is null) as only_in_r3,
        count(*) filter (where r3_mwh is null) as only_in_rf
    from grouped
    group by grouping sets ((fuel_group), ())
),

measure_rows as (
    select
        1 as section,
        measures.item,
        measures.unit_periods,
        measures.units,
        case when gate.is_reached then measures.changed_unit_periods end as changed_unit_periods,
        case when gate.is_reached then round(measures.gross_change_mwh, 3) end as gross_change_mwh,
        case when gate.is_reached then round(measures.r3_absolute_mwh, 3) end as r3_absolute_mwh,
        case when gate.is_reached and measures.r3_absolute_mwh > 0
            then round(measures.gross_change_mwh / measures.r3_absolute_mwh, 6)
        end as gross_share,
        case when gate.is_reached then round(measures.net_change_mwh, 3) end as net_change_mwh,
        case when gate.is_reached and measures.r3_absolute_mwh > 0
            then round(measures.net_change_mwh / measures.r3_absolute_mwh, 6)
        end as net_share,
        case
            when not gate.is_reached then 'population not reached: no result'
            when measures.r3_absolute_mwh = 0 then 'not defined: sum of abs(R3) is 0'
            else 'defined'
        end as share_status,
        measures.only_in_r3,
        measures.only_in_rf,
        gate.dates,
        gate.first_date,
        gate.last_date
    from measures
    cross join gate
),

date_coverage as (
    select
        2 as section,
        'date ' || settlement_date || ': ' || settlement_run_type as item,
        count(*) as unit_periods,
        count(distinct bm_unit) as units,
        count(distinct settlement_period) as periods
    from population_runs
    group by settlement_date, settlement_run_type
),

repeated_cohort_ids as (
    select count(*) as repeated_ids
    from (
        select elexon_bm_unit
        from cohort_fuel_groups
        group by elexon_bm_unit
        having count(*) > 1
    ) as repeated
)

select
    section,
    item,
    unit_periods,
    units,
    changed_unit_periods,
    gross_change_mwh,
    r3_absolute_mwh,
    gross_share,
    net_change_mwh,
    net_share,
    share_status,
    only_in_r3,
    only_in_rf,
    null::bigint as periods,
    dates,
    first_date,
    last_date
from measure_rows

union all

select
    section,
    item,
    unit_periods,
    units,
    null,
    null,
    null,
    null,
    null,
    null,
    null,
    null,
    null,
    periods,
    null,
    null,
    null
from date_coverage

union all

select
    3,
    'Elexon IDs with more than one cohort fuel group (each would count twice)',
    null,
    repeated_ids,
    null,
    null,
    null,
    null,
    null,
    null,
    null,
    null,
    null,
    null,
    null,
    null,
    null
from repeated_cohort_ids

order by section, item
