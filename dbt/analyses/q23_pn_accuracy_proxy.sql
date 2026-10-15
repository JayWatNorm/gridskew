{{ config(group='analysis') }}

-- Q23: how close to their PNs do units run when not instructed? A proxy for
-- NESO's PN-accuracy measure, not that measure: ABSVD and dynamic MEL/MIL
-- are not used, so no threshold is applied and no unit is called
-- inaccurate, and the denominator is the current registry's generation
-- capacity, not NESO's, so the percentages are not comparable with NESO's.
-- Reading rule: research questions, Q23 (12 October 2026, decision 024).
--
-- Population: determinable cohort unit-periods of
-- int_shortfall_by_unit_period with no acceptance in force for any minute
-- (instruction_status 'uninstructed'), interconnectors excluded, up to
-- analysis_read_until. Per unit and calendar month of settlement date:
-- net % = sum(PN - metered) / sum(capacity x 0.5 h) and absolute % =
-- sum(|PN - metered|) / sum(capacity x 0.5 h), capacity being the current
-- registry's generation capacity; a unit without a positive capacity is
-- left out and counted. Section 1 describes the unit-months per fuel group
-- and month (median and 90th percentile); section 2 the declared split,
-- each unit's whole read range on days with an SO-flagged wind bid against
-- days without; section 3 coverage. An SO-flagged wind bid is a stretch of
-- int_elexon__instruction_intervals on a wind unit whose acceptance carries
-- the SO flag and whose instructed energy is below the PN energy over the
-- same stretch (PN integrated as int_shortfall_by_unit_period does), so the
-- flag and the bid belong to one acceptance. Run once, by hand, after the
-- rule is recorded.

{% set read_until = var('analysis_read_until') %}

with capacities as (
    select
        national_grid_bm_unit,
        generation_capacity_mw
    from {{ ref('int_elexon__bm_units_current') }}
),

cohort_periods as (
    select
        shortfall.national_grid_bm_unit,
        shortfall.settlement_date,
        date_trunc('month', shortfall.settlement_date)::date as settlement_month,
        shortfall.determinability,
        shortfall.instruction_status,
        shortfall.metered_run_code,
        shortfall.deviation_from_pn_mwh,
        cohort.fuel_group
    from {{ ref('int_shortfall_by_unit_period') }} as shortfall
    inner join {{ ref('int_elexon__bm_unit_cohort') }} as cohort
        on shortfall.national_grid_bm_unit = cohort.national_grid_bm_unit
{% if read_until is not none %}
    where coalesce(shortfall.period_start_utc, shortfall.settlement_date::timestamp at time zone 'Europe/London')
            <= timestamptz '{{ read_until }}'
{% endif %}
),

so_flagged_wind_stretches as (
    select
        intervals.national_grid_bm_unit,
        intervals.period_start_utc,
        intervals.interval_from,
        intervals.interval_to,
        intervals.instructed_mwh
    from {{ ref('int_elexon__instruction_intervals') }} as intervals
    inner join {{ ref('int_elexon__bm_unit_cohort') }} as cohort
        on intervals.national_grid_bm_unit = cohort.national_grid_bm_unit
    -- A placeholder row marks a half-hour with no stretch left.
    where intervals.interval_from is not null
        and cohort.fuel_group = 'wind'
        and intervals.so_flag
{% if read_until is not none %}
        and intervals.period_start_utc <= timestamptz '{{ read_until }}'
{% endif %}
),

so_flagged_wind_stretch_energy as (
    select
        stretches.national_grid_bm_unit,
        stretches.interval_from,
        commitments.settlement_date,
        stretches.instructed_mwh,
        sum(
            {{ ramp_mwh(
                'segments.level_from', 'segments.level_to',
                'segments.time_from', 'segments.time_to',
                'stretches.interval_from', 'stretches.interval_to'
            ) }}
        ) as pn_mwh
    from so_flagged_wind_stretches as stretches
    inner join {{ ref('fct_commitments') }} as commitments
        on stretches.national_grid_bm_unit = commitments.national_grid_bm_unit
        and stretches.period_start_utc = commitments.period_start_utc
    inner join {{ ref('stg_elexon__pn') }} as segments
        on segments.national_grid_bm_unit = commitments.national_grid_bm_unit
        and segments.settlement_date = commitments.settlement_date
        and segments.settlement_period = commitments.settlement_period
        and segments.retrieved_at = commitments.pn_retrieved_at
        and segments.time_to > segments.time_from
        and segments.time_from < stretches.interval_to
        and segments.time_to > stretches.interval_from
    where commitments.coverage_status = 'complete'
    group by
        stretches.national_grid_bm_unit,
        stretches.interval_from,
        commitments.settlement_date,
        stretches.instructed_mwh
),

-- A day with at least one SO-flagged wind acceptance below the PN: a bid.
flagged_days as (
    select distinct settlement_date
    from so_flagged_wind_stretch_energy
    where instructed_mwh < pn_mwh
),

population as (
    select
        cohort_periods.national_grid_bm_unit,
        cohort_periods.settlement_date,
        cohort_periods.settlement_month,
        cohort_periods.fuel_group,
        cohort_periods.metered_run_code,
        cohort_periods.deviation_from_pn_mwh,
        capacities.generation_capacity_mw,
        flagged_days.settlement_date is not null as is_flagged_day
    from cohort_periods
    left join capacities
        on cohort_periods.national_grid_bm_unit = capacities.national_grid_bm_unit
    left join flagged_days
        on cohort_periods.settlement_date = flagged_days.settlement_date
    where cohort_periods.determinability = 'determinable'
        and cohort_periods.instruction_status = 'uninstructed'
        and cohort_periods.fuel_group <> 'interconnector'
),

measured as (
    select
        national_grid_bm_unit,
        settlement_date,
        settlement_month,
        fuel_group,
        is_flagged_day,
        deviation_from_pn_mwh,
        generation_capacity_mw * 0.5 as capacity_mwh
    from population
    where generation_capacity_mw > 0
),

unit_months as (
    select
        national_grid_bm_unit,
        fuel_group,
        settlement_month,
        count(*) as half_hours,
        100 * sum(deviation_from_pn_mwh) / sum(capacity_mwh) as net_pct,
        100 * sum(abs(deviation_from_pn_mwh)) / sum(capacity_mwh) as absolute_pct
    from measured
    group by national_grid_bm_unit, fuel_group, settlement_month
),

unit_splits as (
    select
        national_grid_bm_unit,
        fuel_group,
        is_flagged_day,
        count(*) as half_hours,
        100 * sum(deviation_from_pn_mwh) / sum(capacity_mwh) as net_pct,
        100 * sum(abs(deviation_from_pn_mwh)) / sum(capacity_mwh) as absolute_pct
    from measured
    group by national_grid_bm_unit, fuel_group, is_flagged_day
),

by_month as (
    select
        1 as section,
        fuel_group,
        settlement_month,
        'unit-months' as item,
        count(*) as units,
        sum(half_hours) as half_hours,
        round((percentile_cont(0.5) within group (order by net_pct))::numeric, 3) as median_net_pct,
        round((percentile_cont(0.9) within group (order by net_pct))::numeric, 3) as p90_net_pct,
        round((percentile_cont(0.5) within group (order by absolute_pct))::numeric, 3) as median_absolute_pct,
        round((percentile_cont(0.9) within group (order by absolute_pct))::numeric, 3) as p90_absolute_pct
    from unit_months
    group by fuel_group, settlement_month
),

by_split as (
    select
        2 as section,
        fuel_group,
        null::date as settlement_month,
        case
            when is_flagged_day then 'units on days with an SO-flagged wind bid'
            else 'units on days without'
        end as item,
        count(*) as units,
        sum(half_hours) as half_hours,
        round((percentile_cont(0.5) within group (order by net_pct))::numeric, 3) as median_net_pct,
        round((percentile_cont(0.9) within group (order by net_pct))::numeric, 3) as p90_net_pct,
        round((percentile_cont(0.5) within group (order by absolute_pct))::numeric, 3) as median_absolute_pct,
        round((percentile_cont(0.9) within group (order by absolute_pct))::numeric, 3) as p90_absolute_pct
    from unit_splits
    group by fuel_group, is_flagged_day
),

days_in_range as (
    select distinct settlement_date
    from population
),

split_days as (
    select
        2 as section,
        null::text as fuel_group,
        null::date as settlement_month,
        case
            when flagged_days.settlement_date is not null
                then 'days with an SO-flagged wind bid'
            else 'days without'
        end as item,
        null::bigint as units,
        count(*) as half_hours,
        null::numeric as median_net_pct,
        null::numeric as p90_net_pct,
        null::numeric as median_absolute_pct,
        null::numeric as p90_absolute_pct
    from days_in_range
    left join flagged_days
        on days_in_range.settlement_date = flagged_days.settlement_date
    group by 4
),

coverage as (
    select
        3 as section,
        fuel_group,
        null::date as settlement_month,
        'left out: no positive registry capacity' as item,
        count(distinct national_grid_bm_unit) as units,
        count(*) as half_hours,
        null::numeric as median_net_pct,
        null::numeric as p90_net_pct,
        null::numeric as median_absolute_pct,
        null::numeric as p90_absolute_pct
    from population
    where generation_capacity_mw is null
        or generation_capacity_mw <= 0
    group by fuel_group

    union all

    select
        3,
        null,
        null,
        'cohort unit-periods: ' || coalesce(determinability, '(none)') || ', '
            || coalesce(instruction_status, '(none)')
            || case when fuel_group = 'interconnector' then ', interconnector' else '' end,
        count(distinct national_grid_bm_unit),
        count(*),
        null,
        null,
        null,
        null
    from cohort_periods
    group by 4

    union all

    select
        3,
        null,
        null,
        'population by metered run: ' || metered_run_code,
        count(distinct national_grid_bm_unit),
        count(*),
        null,
        null,
        null,
        null
    from population
    where generation_capacity_mw > 0
    group by metered_run_code
)

select
    section,
    fuel_group,
    settlement_month,
    item,
    units,
    half_hours,
    median_net_pct,
    p90_net_pct,
    median_absolute_pct,
    p90_absolute_pct
from by_month
union all
select
    section,
    fuel_group,
    settlement_month,
    item,
    units,
    half_hours,
    median_net_pct,
    p90_net_pct,
    median_absolute_pct,
    p90_absolute_pct
from by_split
union all
select
    section,
    fuel_group,
    settlement_month,
    item,
    units,
    half_hours,
    median_net_pct,
    p90_net_pct,
    median_absolute_pct,
    p90_absolute_pct
from split_days
union all
select
    section,
    fuel_group,
    settlement_month,
    item,
    units,
    half_hours,
    median_net_pct,
    p90_net_pct,
    median_absolute_pct,
    p90_absolute_pct
from coverage
order by section, fuel_group, settlement_month, item
