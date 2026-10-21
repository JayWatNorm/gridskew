{{ config(group='analysis') }}

-- Q4: are half-hours with a larger carbon forecast error associated with
-- more unexplained shortfall? Reading rule (research questions, amended
-- 12 October 2026 before any read):
--
-- Population: half-hours on matched days, a matched day being a settlement
-- date with both error_final and the cohort's unexplained shortfall for at
-- least 46 half-hours; at least 90 matched days, up to a fixed
-- analysis_read_until. Measure: per half-hour, the cohort sum of the positive
-- part of unexplained_shortfall_mwh (under-delivery, as Q5). Test: quintiles
-- of abs(error_final), ties broken by period start; "supported" if the top
-- quintile's mean is at least 25% above the bottom quintile's, otherwise "not
-- supported". If the bottom quintile's mean is 0 or less, both means and
-- their difference are reported and the ratio test and its interval are not
-- applicable. A 90% day-block bootstrap interval (1,000 draws) for the ratio
-- is reported beside the verdict and does not change it; a draw whose bottom
-- mean is not positive forms no ratio, and with fewer than 900 such ratios
-- the interval is unavailable. Read once, on the first day the population
-- reaches 90 matched days.
--
-- A half-hour has the cohort's unexplained shortfall when at least one
-- determinable cohort unit-period holds a value (Q5's population: with or
-- without a PN; before ABSVD). Quintiles are ntile(5), so the lower quintiles
-- take one extra half-hour when the count is not a multiple of 5. Each
-- bootstrap draw takes as many matched days as there are, with replacement;
-- its picks come from md5 of a fixed seed, the draw and the pick, so every
-- run gives the same interval. The interval resamples whole days for the
-- correlation within a day; correlation between days is not modelled. Until
-- the population is reached it returns counts and no result.

{% set days_needed = var('shortfall_error_matched_days_needed') %}
{% set read_until = var('analysis_read_until') %}
{% set matched_day_min_half_hours = 46 %}
{% set bootstrap_draws = 1000 %}
{% set bootstrap_min_usable_draws = var('shortfall_error_bootstrap_min_usable_draws') %}
{% set bootstrap_seed = 20261012 %}

with errors as (
    select
        period_start,
        error_final
    from {{ ref('int_carbon_error_by_period') }}
    where error_final is not null
{% if read_until is not none %}
        and period_start <= timestamptz '{{ read_until }}'
{% endif %}
),

cohort_shortfall as (
    select
        shortfall.settlement_date,
        shortfall.period_start_utc as period_start,
        sum(greatest(shortfall.unexplained_shortfall_mwh, 0)) as positive_shortfall_mwh,
        count(*) as unit_periods
    from {{ ref('int_shortfall_by_unit_period') }} as shortfall
    inner join {{ ref('int_elexon__bm_unit_cohort') }} as cohort
        on shortfall.national_grid_bm_unit = cohort.national_grid_bm_unit
    where shortfall.determinability = 'determinable'
        and shortfall.unexplained_shortfall_mwh is not null
        and shortfall.period_start_utc is not null
{% if read_until is not none %}
        and shortfall.period_start_utc <= timestamptz '{{ read_until }}'
{% endif %}
    group by shortfall.settlement_date, shortfall.period_start_utc
),

both_values as (
    select
        cohort_shortfall.settlement_date,
        cohort_shortfall.period_start,
        errors.error_final,
        cohort_shortfall.positive_shortfall_mwh,
        cohort_shortfall.unit_periods
    from cohort_shortfall
    inner join errors
        on cohort_shortfall.period_start = errors.period_start
),

matched_days as (
    select
        settlement_date,
        row_number() over (order by settlement_date) as day_number
    from both_values
    group by settlement_date
    having count(*) >= {{ matched_day_min_half_hours }}
),

population as (
    select
        both_values.settlement_date,
        both_values.period_start,
        both_values.error_final,
        both_values.positive_shortfall_mwh,
        both_values.unit_periods,
        matched_days.day_number
    from both_values
    inner join matched_days
        on both_values.settlement_date = matched_days.settlement_date
),

gate as (
    select
        count(*) as matched_days,
        count(*) >= {{ days_needed }} as is_reached
    from matched_days
),

quintiles as (
    select
        period_start,
        error_final,
        positive_shortfall_mwh,
        ntile(5) over (order by abs(error_final), period_start) as quintile
    from population
),

quintile_means as (
    select
        quintile,
        count(*) as half_hours,
        min(abs(error_final)) as min_abs_error_final,
        max(abs(error_final)) as max_abs_error_final,
        sum(positive_shortfall_mwh) as sum_positive_shortfall_mwh,
        avg(positive_shortfall_mwh) as mean_positive_shortfall_mwh
    from quintiles
    group by quintile
),

observed as (
    select
        bottom.half_hours as bottom_half_hours,
        bottom.sum_positive_shortfall_mwh as bottom_sum,
        bottom.mean_positive_shortfall_mwh as bottom_mean,
        top.half_hours as top_half_hours,
        top.sum_positive_shortfall_mwh as top_sum,
        top.mean_positive_shortfall_mwh as top_mean
    from quintile_means as bottom
    inner join quintile_means as top
        on bottom.quintile = 1
        and top.quintile = 5
),

picks as (
    select
        draws.draw,
        picked.pick,
        1 + mod(
            ('x' || substr(md5('{{ bootstrap_seed }}:' || draws.draw || ':' || picked.pick), 1, 8))::bit(32)::bigint,
            gate.matched_days
        ) as day_number
    from gate
    cross join observed
    cross join generate_series(1, {{ bootstrap_draws }}) as draws (draw)
    cross join generate_series(1, gate.matched_days) as picked (pick)
    where gate.is_reached
        and observed.bottom_sum > 0
),

resampled_quintiles as (
    select
        picks.draw,
        population.positive_shortfall_mwh,
        ntile(5) over (
            partition by picks.draw
            order by abs(population.error_final), population.period_start, picks.pick
        ) as quintile
    from picks
    inner join population
        on picks.day_number = population.day_number
),

draw_means as (
    select
        draw,
        avg(positive_shortfall_mwh) filter (where quintile = 1) as bottom_mean,
        avg(positive_shortfall_mwh) filter (where quintile = 5) as top_mean
    from resampled_quintiles
    group by draw
),

bootstrap as (
    select
        count(*) as usable_draws,
        percentile_cont(0.05) within group (order by top_mean / bottom_mean) as interval_low,
        percentile_cont(0.95) within group (order by top_mean / bottom_mean) as interval_high
    from draw_means
    where bottom_mean > 0
),

outcome as (
    select
        gate.matched_days,
        gate.is_reached,
        observed.bottom_mean,
        observed.top_mean,
        case
            when not gate.is_reached then 'population not reached: no result'
            when observed.bottom_sum <= 0 then 'not applicable: bottom quintile mean is 0 or less'
            -- top mean >= 1.25 x bottom mean, on exact sums and counts
            when observed.top_sum * observed.bottom_half_hours
                >= 1.25 * observed.bottom_sum * observed.top_half_hours then 'supported'
            else 'not supported'
        end as verdict,
        case
            when not gate.is_reached then null
            when observed.bottom_sum <= 0 then 'not applicable'
            when bootstrap.usable_draws >= {{ bootstrap_min_usable_draws }} then 'available'
            else 'unavailable'
        end as interval_status,
        bootstrap.usable_draws,
        bootstrap.interval_low,
        bootstrap.interval_high
    from gate
    cross join bootstrap
    left join observed
        on true
),

population_summary as (
    select
        count(*) as half_hours,
        min(settlement_date) as first_date,
        max(settlement_date) as last_date
    from population
),

verdict_rows as (
    select
        1 as section,
        'population: matched days ({{ days_needed }} needed)' as item,
        null::integer as quintile,
        population_summary.half_hours,
        outcome.matched_days as days,
        population_summary.first_date,
        population_summary.last_date,
        null::numeric as value,
        case when outcome.is_reached then 'reached' else 'not reached' end as result
    from outcome
    cross join population_summary

    union all

    select 1, 'bottom quintile mean, MWh', null, null, null, null, null,
        case when is_reached then round(bottom_mean, 6) end, null
    from outcome

    union all

    select 1, 'top quintile mean, MWh', null, null, null, null, null,
        case when is_reached then round(top_mean, 6) end, null
    from outcome

    union all

    select 1, 'difference, top minus bottom, MWh', null, null, null, null, null,
        case when is_reached then round(top_mean - bottom_mean, 6) end, null
    from outcome

    union all

    select 1, 'ratio, top over bottom', null, null, null, null, null,
        case when is_reached and bottom_mean > 0 then round(top_mean / bottom_mean, 6) end, null
    from outcome

    union all

    select 1, 'verdict (supported at a ratio of 1.25 or more)', null, null, null, null, null,
        null, verdict
    from outcome

    union all

    select 1, 'bootstrap draws with a ratio ({{ bootstrap_min_usable_draws }} of {{ bootstrap_draws }} needed)',
        null, null, null, null, null,
        case when interval_status in ('available', 'unavailable') then usable_draws end, interval_status
    from outcome

    union all

    select 1, 'bootstrap 90% interval, low', null, null, null, null, null,
        case when interval_status = 'available' then round(interval_low::numeric, 6) end, null
    from outcome

    union all

    select 1, 'bootstrap 90% interval, high', null, null, null, null, null,
        case when interval_status = 'available' then round(interval_high::numeric, 6) end, null
    from outcome
),

quintile_rows as (
    select
        2 as section,
        'quintile of abs(error_final): mean positive shortfall, MWh' as item,
        listed.quintile,
        quintile_means.half_hours,
        null::bigint as days,
        null::date as first_date,
        null::date as last_date,
        case when outcome.is_reached then round(quintile_means.mean_positive_shortfall_mwh, 6) end as value,
        case when outcome.is_reached
            then 'abs(error_final) ' || quintile_means.min_abs_error_final
                || ' to ' || quintile_means.max_abs_error_final
        end as result
    from generate_series(1, 5) as listed (quintile)
    cross join outcome
    left join quintile_means
        on listed.quintile = quintile_means.quintile
),

error_dates as (
    select distinct (period_start at time zone 'Europe/London')::date as settlement_date
    from errors
),

coverage_rows as (
    select
        3 as section,
        'settlement dates with an error_final' as item,
        null::integer as quintile,
        null::bigint as half_hours,
        count(*) as days,
        min(settlement_date) as first_date,
        max(settlement_date) as last_date,
        null::numeric as value,
        null::text as result
    from error_dates

    union all

    select 3, 'settlement dates with the cohort measure', null, null,
        count(distinct settlement_date), min(settlement_date), max(settlement_date), null, null
    from cohort_shortfall

    union all

    select 3, 'settlement dates with both values in any half-hour', null, null,
        count(distinct settlement_date), min(settlement_date), max(settlement_date), null, null
    from both_values

    union all

    select 3, 'on matched days: cohort measure without error_final', null, count(*),
        count(distinct cohort_shortfall.settlement_date), null, null, null, null
    from cohort_shortfall
    inner join matched_days
        on cohort_shortfall.settlement_date = matched_days.settlement_date
    left join errors
        on cohort_shortfall.period_start = errors.period_start
    where errors.period_start is null

    union all

    select 3, 'on matched days: error_final without the cohort measure', null, count(*),
        count(distinct matched_days.settlement_date), null, null, null, null
    from errors
    inner join matched_days
        on (errors.period_start at time zone 'Europe/London')::date = matched_days.settlement_date
    left join cohort_shortfall
        on errors.period_start = cohort_shortfall.period_start
    where cohort_shortfall.period_start is null

    union all

    select 3, 'cohort unit-periods per population half-hour: min', null, count(*), null, null, null,
        min(unit_periods), null
    from population

    union all

    select 3, 'cohort unit-periods per population half-hour: median', null, count(*), null, null, null,
        percentile_cont(0.5) within group (order by unit_periods)::numeric, null
    from population

    union all

    select 3, 'cohort unit-periods per population half-hour: max', null, count(*), null, null, null,
        max(unit_periods), null
    from population
),

all_rows as (
    select * from verdict_rows
    union all
    select * from quintile_rows
    union all
    select * from coverage_rows
)

select
    section,
    item,
    quintile,
    half_hours,
    days,
    first_date,
    last_date,
    value,
    result
from all_rows
order by section, item, quintile
