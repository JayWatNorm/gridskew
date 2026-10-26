{{ config(group='analysis') }}

-- Q8 and Q28 on a year of history: one shared population, read once.
-- Reading rules (research questions, registered 25 October 2026, before any
-- demand value was held):
--
-- Window: settlement dates demand_history_from to demand_history_until, both
-- included (22 August 2025 to 16 August 2026). It is fixed, and ends the day
-- before the carbon forecast archive starts. Carbon error: error_api_final,
-- the outturn minus the API's own final forecast, which is made minutes
-- before the half-hour and is the only carbon forecast held for that year.
-- It is taken from int_carbon_outturn__latest, because
-- int_carbon_error_by_period holds only half-hours with an archived
-- forecast. Demand error: demand_error_final_mw, the first INDO outturn
-- minus the last national NDF issued before the half-hour starts. Shortfall:
-- per half-hour, the cohort sum of the positive part of
-- unexplained_shortfall_mwh over determinable unit-periods, as Q4.
--
-- Population: matched days are settlement dates in the window with all three
-- values in at least 46 half-hours; the population is every half-hour on
-- those days with all three.
--
-- Q8 (section 2): Spearman rank correlations of abs(error_api_final) with
-- abs(demand_error_final_mw) (rho_demand) and with the shortfall
-- (rho_shortfall), each with n. Tied values take their average rank, and
-- each correlation is the Pearson correlation of the ranks. The gap is
-- rho_demand minus rho_shortfall: demand is stronger at a gap of 0.1 or
-- more, shortfall at -0.1 or less, otherwise there is no clear difference.
--
-- Q28 (sections 3 and 4): Q4's test on abs(error_api_final). Quintiles are
-- ntile(5), so the lower quintiles take one extra half-hour when the count
-- is not a multiple of 5. Ties are ordered by quintile_tie_order (amended
-- 26 October 2026, before any read, with Q4): the errors are whole
-- gCO2/kWh, and a date order would fill the bottom quintile with the
-- earliest of many tied half-hours. Section 4 reports how many half-hours
-- share the bottom quintile's largest error. Supported if the
-- top quintile's mean shortfall is at least 25% above the bottom
-- quintile's, otherwise not supported; if the bottom mean is 0 or less, the
-- means and their difference are reported and the test is not applicable.
--
-- Intervals, beside each verdict and never changing it: a 90% percentile
-- interval from a day-block bootstrap of 1,000 draws. Each draw takes as
-- many matched days as there are, with replacement, and recomputes both
-- correlations and the quintiles on the same resampled half-hours. A draw
-- where a measure is constant forms no gap; a draw whose bottom quintile
-- mean is not positive forms no ratio; with fewer than
-- demand_history_bootstrap_min_usable_draws usable draws an interval is
-- unavailable. The picks come from md5 of a fixed seed, the draw and the
-- pick, so every run gives the same intervals. Resampling whole days keeps
-- the correlation within a day; correlation between days is not modelled.
--
-- Read once, after the demand back-fill and its counts-only coverage check,
-- with n, the window and the coverage (section 5) beside every number. There
-- is no gate on a count: the window is fixed. Section 5 also counts
-- population half-hours whose forecast is over 30 minutes old or whose
-- outturn came over an hour late, which a publish day missing from the
-- back-fill would cause; they are described, not excluded.

{% set history_from = var('demand_history_from') %}
{% set history_until = var('demand_history_until') %}
{% set matched_day_min_half_hours = 46 %}
{% set bootstrap_draws = 1000 %}
{% set bootstrap_min_usable_draws = var('demand_history_bootstrap_min_usable_draws') %}
{% set bootstrap_seed = 20261025 %}
{% set stale_forecast_minutes = 30 %}
{% set late_outturn_minutes = 60 %}

with window_days as (
    select date '{{ history_from }}' + day_offset as settlement_date
    from generate_series(0, date '{{ history_until }}' - date '{{ history_from }}') as offsets (day_offset)
),

window_half_hours as (
    select
        window_days.settlement_date,
        {{ settlement_period_start_utc('window_days.settlement_date', 'periods.settlement_period') }} as period_start
    from window_days
    cross join lateral generate_series(
        1, {{ settlement_period_count('window_days.settlement_date') }}
    ) as periods (settlement_period)
),

carbon_errors as (
    select
        period_start,
        actual - api_final_forecast as error_api_final
    from {{ ref('int_carbon_outturn__latest') }}
    where api_final_forecast is not null
),

demand_errors as (
    select
        start_time as period_start,
        demand_error_final_mw,
        forecast_published_at,
        outturn_published_at
    from {{ ref('int_demand_error_by_period') }}
    where demand_error_final_mw is not null
),

cohort_shortfall as (
    select
        shortfall.period_start_utc as period_start,
        sum(greatest(shortfall.unexplained_shortfall_mwh, 0)) as positive_shortfall_mwh
    from {{ ref('int_shortfall_by_unit_period') }} as shortfall
    inner join {{ ref('int_elexon__bm_unit_cohort') }} as cohort
        on shortfall.national_grid_bm_unit = cohort.national_grid_bm_unit
    where shortfall.determinability = 'determinable'
        and shortfall.unexplained_shortfall_mwh is not null
        and shortfall.settlement_date between date '{{ history_from }}' and date '{{ history_until }}'
    group by shortfall.period_start_utc
),

window_inputs as (
    select
        window_half_hours.settlement_date,
        window_half_hours.period_start,
        carbon_errors.error_api_final,
        demand_errors.demand_error_final_mw,
        cohort_shortfall.positive_shortfall_mwh,
        extract(epoch from window_half_hours.period_start - demand_errors.forecast_published_at) / 60
            as forecast_age_minutes,
        extract(epoch from demand_errors.outturn_published_at - window_half_hours.period_start) / 60 - 30
            as outturn_delay_minutes,
        carbon_errors.error_api_final is not null
            and demand_errors.demand_error_final_mw is not null
            and cohort_shortfall.positive_shortfall_mwh is not null as has_all_three
    from window_half_hours
    left join carbon_errors
        on window_half_hours.period_start = carbon_errors.period_start
    left join demand_errors
        on window_half_hours.period_start = demand_errors.period_start
    left join cohort_shortfall
        on window_half_hours.period_start = cohort_shortfall.period_start
),

matched_days as (
    select
        settlement_date,
        row_number() over (order by settlement_date) as day_number
    from window_inputs
    where has_all_three
    group by settlement_date
    having count(*) >= {{ matched_day_min_half_hours }}
),

population as (
    select
        window_inputs.settlement_date,
        window_inputs.period_start,
        abs(window_inputs.error_api_final) as abs_carbon_error,
        abs(window_inputs.demand_error_final_mw) as abs_demand_error,
        window_inputs.positive_shortfall_mwh,
        matched_days.day_number
    from window_inputs
    inner join matched_days
        on window_inputs.settlement_date = matched_days.settlement_date
    where window_inputs.has_all_three
),

picks as (
    select
        draws.draw,
        picked.pick,
        1 + mod(
            ('x' || substr(md5('{{ bootstrap_seed }}:' || draws.draw || ':' || picked.pick), 1, 8))::bit(32)::bigint,
            day_count.days
        ) as day_number
    from (select count(*) as days from matched_days) as day_count
    cross join generate_series(1, {{ bootstrap_draws }}) as draws (draw)
    cross join generate_series(1, day_count.days) as picked (pick)
),

-- Draw 0 is the population itself; draws 1 to 1,000 resample it by day.
samples as (
    select
        0 as draw,
        0 as pick,
        period_start,
        abs_carbon_error,
        abs_demand_error,
        positive_shortfall_mwh
    from population

    union all

    select
        picks.draw,
        picks.pick,
        population.period_start,
        population.abs_carbon_error,
        population.abs_demand_error,
        population.positive_shortfall_mwh
    from picks
    inner join population
        on picks.day_number = population.day_number
),

-- rank() is the first position of a run of tied values and count(*) over the
-- same ordering is its last, because the default frame takes in the current
-- row's ties; their mean is the average rank.
sample_ranks as (
    select
        draw,
        abs_carbon_error,
        positive_shortfall_mwh,
        (rank() over by_carbon + count(*) over by_carbon) / 2.0 as carbon_rank,
        (rank() over by_demand + count(*) over by_demand) / 2.0 as demand_rank,
        (rank() over by_shortfall + count(*) over by_shortfall) / 2.0 as shortfall_rank,
        ntile(5) over (
            partition by draw
            order by
                abs_carbon_error,
                {{ quintile_tie_order(bootstrap_seed, 'draw', 'pick', 'period_start') }},
                period_start,
                pick
        ) as quintile
    from samples
    window
        by_carbon as (partition by draw order by abs_carbon_error),
        by_demand as (partition by draw order by abs_demand_error),
        by_shortfall as (partition by draw order by positive_shortfall_mwh)
),

sample_results as (
    select
        draw,
        count(*) as half_hours,
        corr(carbon_rank, demand_rank) as rho_demand,
        corr(carbon_rank, shortfall_rank) as rho_shortfall,
        count(*) filter (where quintile = 1) as bottom_half_hours,
        sum(positive_shortfall_mwh) filter (where quintile = 1) as bottom_sum,
        count(*) filter (where quintile = 5) as top_half_hours,
        sum(positive_shortfall_mwh) filter (where quintile = 5) as top_sum
    from sample_ranks
    group by draw
),

observed as (
    select
        population_summary.half_hours,
        population_summary.days,
        population_summary.first_date,
        population_summary.last_date,
        sample_results.rho_demand,
        sample_results.rho_shortfall,
        sample_results.rho_demand - sample_results.rho_shortfall as gap,
        sample_results.bottom_half_hours,
        sample_results.bottom_sum,
        sample_results.top_half_hours,
        sample_results.top_sum,
        sample_results.bottom_sum / sample_results.bottom_half_hours as bottom_mean,
        sample_results.top_sum / sample_results.top_half_hours as top_mean
    from (
        select
            count(*) as half_hours,
            count(distinct settlement_date) as days,
            min(settlement_date) as first_date,
            max(settlement_date) as last_date
        from population
    ) as population_summary
    left join sample_results
        on sample_results.draw = 0
),

gap_bootstrap as (
    select
        count(*) as usable_draws,
        percentile_cont(0.05) within group (order by rho_demand - rho_shortfall) as interval_low,
        percentile_cont(0.95) within group (order by rho_demand - rho_shortfall) as interval_high
    from sample_results
    where draw > 0
        and rho_demand is not null
        and rho_shortfall is not null
),

ratio_bootstrap as (
    select
        count(*) as usable_draws,
        percentile_cont(0.05) within group (
            order by (top_sum / top_half_hours) / (bottom_sum / bottom_half_hours)
        ) as interval_low,
        percentile_cont(0.95) within group (
            order by (top_sum / top_half_hours) / (bottom_sum / bottom_half_hours)
        ) as interval_high
    from sample_results
    where draw > 0
        and bottom_sum > 0
),

outcome as (
    select
        observed.*,
        case
            when observed.half_hours = 0 then 'no matched days: no result'
            when observed.rho_demand is null or observed.rho_shortfall is null
                then 'not applicable: a measure is constant'
            when observed.gap >= 0.1 then 'demand stronger'
            when observed.gap <= -0.1 then 'shortfall stronger'
            else 'no clear difference'
        end as q8_verdict,
        case
            when observed.half_hours = 0 then 'no matched days: no result'
            when observed.bottom_sum <= 0 then 'not applicable: bottom quintile mean is 0 or less'
            -- top mean >= 1.25 x bottom mean, on exact sums and counts
            when observed.top_sum * observed.bottom_half_hours
                >= 1.25 * observed.bottom_sum * observed.top_half_hours then 'supported'
            else 'not supported'
        end as q28_verdict,
        case
            when observed.rho_demand is null or observed.rho_shortfall is null then 'not applicable'
            when gap_bootstrap.usable_draws >= {{ bootstrap_min_usable_draws }} then 'available'
            else 'unavailable'
        end as gap_interval_status,
        gap_bootstrap.usable_draws as gap_usable_draws,
        gap_bootstrap.interval_low as gap_interval_low,
        gap_bootstrap.interval_high as gap_interval_high,
        case
            when observed.bottom_sum is null or observed.bottom_sum <= 0 then 'not applicable'
            when ratio_bootstrap.usable_draws >= {{ bootstrap_min_usable_draws }} then 'available'
            else 'unavailable'
        end as ratio_interval_status,
        ratio_bootstrap.usable_draws as ratio_usable_draws,
        ratio_bootstrap.interval_low as ratio_interval_low,
        ratio_bootstrap.interval_high as ratio_interval_high
    from observed
    cross join gap_bootstrap
    cross join ratio_bootstrap
),

population_rows as (
    select
        1 as section,
        'population: matched days (all three values in at least {{ matched_day_min_half_hours }} half-hours), every half-hour on them with all three' as item,
        null::integer as quintile,
        half_hours,
        days,
        first_date,
        last_date,
        null::numeric as value,
        null::text as result
    from outcome
),

q8_rows as (
    select
        2 as section,
        'rho_demand: abs(error_api_final) with abs(demand_error_final_mw)' as item,
        null::integer as quintile,
        half_hours,
        null::bigint as days,
        null::date as first_date,
        null::date as last_date,
        round(rho_demand::numeric, 6) as value,
        null::text as result
    from outcome

    union all

    select 2, 'rho_shortfall: abs(error_api_final) with positive shortfall', null, half_hours,
        null, null, null, round(rho_shortfall::numeric, 6), null
    from outcome

    union all

    select 2, 'gap, rho_demand minus rho_shortfall', null, null, null, null, null,
        round(gap::numeric, 6), null
    from outcome

    union all

    select 2, 'verdict (stronger at a gap of 0.1 or more either way)', null, null, null, null, null,
        null, q8_verdict
    from outcome

    union all

    select 2, 'bootstrap draws with a gap ({{ bootstrap_min_usable_draws }} of {{ bootstrap_draws }} needed)',
        null, null, null, null, null,
        case when gap_interval_status <> 'not applicable' then gap_usable_draws end, gap_interval_status
    from outcome

    union all

    select 2, 'bootstrap 90% interval for the gap, low', null, null, null, null, null,
        case when gap_interval_status = 'available' then round(gap_interval_low::numeric, 6) end, null
    from outcome

    union all

    select 2, 'bootstrap 90% interval for the gap, high', null, null, null, null, null,
        case when gap_interval_status = 'available' then round(gap_interval_high::numeric, 6) end, null
    from outcome
),

q28_rows as (
    select
        3 as section,
        'bottom quintile mean, MWh' as item,
        null::integer as quintile,
        bottom_half_hours as half_hours,
        null::bigint as days,
        null::date as first_date,
        null::date as last_date,
        round(bottom_mean, 6) as value,
        null::text as result
    from outcome

    union all

    select 3, 'top quintile mean, MWh', null, top_half_hours, null, null, null,
        round(top_mean, 6), null
    from outcome

    union all

    select 3, 'difference, top minus bottom, MWh', null, null, null, null, null,
        round(top_mean - bottom_mean, 6), null
    from outcome

    union all

    select 3, 'ratio, top over bottom', null, null, null, null, null,
        case when bottom_sum > 0 then round(top_mean / bottom_mean, 6) end, null
    from outcome

    union all

    select 3, 'verdict (supported at a ratio of 1.25 or more)', null, null, null, null, null,
        null, q28_verdict
    from outcome

    union all

    select 3, 'bootstrap draws with a ratio ({{ bootstrap_min_usable_draws }} of {{ bootstrap_draws }} needed)',
        null, null, null, null, null,
        case when ratio_interval_status <> 'not applicable' then ratio_usable_draws end, ratio_interval_status
    from outcome

    union all

    select 3, 'bootstrap 90% interval for the ratio, low', null, null, null, null, null,
        case when ratio_interval_status = 'available' then round(ratio_interval_low::numeric, 6) end, null
    from outcome

    union all

    select 3, 'bootstrap 90% interval for the ratio, high', null, null, null, null, null,
        case when ratio_interval_status = 'available' then round(ratio_interval_high::numeric, 6) end, null
    from outcome
),

quintile_means as (
    select
        quintile,
        count(*) as half_hours,
        min(abs_carbon_error) as min_abs_carbon_error,
        max(abs_carbon_error) as max_abs_carbon_error,
        avg(positive_shortfall_mwh) as mean_positive_shortfall_mwh
    from sample_ranks
    where draw = 0
    group by quintile
),

-- How many half-hours share the bottom quintile's largest error, and how
-- many of them the tie order put in the bottom quintile.
bottom_boundary_ties as (
    select
        count(*) as half_hours,
        count(*) filter (where quintile = 1) as in_bottom,
        max(abs_carbon_error) as boundary_abs_carbon_error
    from sample_ranks
    where draw = 0
        and abs_carbon_error = (select max(abs_carbon_error) from sample_ranks where draw = 0 and quintile = 1)
),

quintile_rows as (
    select
        4 as section,
        'quintile of abs(error_api_final): mean positive shortfall, MWh' as item,
        listed.quintile,
        quintile_means.half_hours,
        null::bigint as days,
        null::date as first_date,
        null::date as last_date,
        round(quintile_means.mean_positive_shortfall_mwh, 6) as value,
        'abs(error_api_final) ' || quintile_means.min_abs_carbon_error
            || ' to ' || quintile_means.max_abs_carbon_error as result
    from generate_series(1, 5) as listed (quintile)
    left join quintile_means
        on listed.quintile = quintile_means.quintile

    union all

    select 4, 'half-hours sharing the bottom quintile''s largest abs(error_api_final) (value: that error)', null,
        half_hours, null, null, null, boundary_abs_carbon_error,
        'in the bottom quintile: ' || in_bottom
    from bottom_boundary_ties
),

window_summary as (
    select
        count(distinct period_start) as half_hours,
        count(distinct settlement_date) as days,
        min(settlement_date) as first_date,
        max(settlement_date) as last_date,
        count(error_api_final) as carbon_half_hours,
        count(demand_error_final_mw) as demand_half_hours,
        count(positive_shortfall_mwh) as shortfall_half_hours,
        count(*) filter (where has_all_three) as all_three_half_hours
    from window_inputs
),

dates_with_each_input as (
    select
        count(*) filter (where carbon_half_hours = 0) as without_carbon,
        count(*) filter (where demand_half_hours = 0) as without_demand,
        count(*) filter (where shortfall_half_hours = 0) as without_shortfall,
        count(*) filter (where all_three_half_hours > 0) as with_all_three
    from (
        select
            settlement_date,
            count(error_api_final) as carbon_half_hours,
            count(demand_error_final_mw) as demand_half_hours,
            count(positive_shortfall_mwh) as shortfall_half_hours,
            count(*) filter (where has_all_three) as all_three_half_hours
        from window_inputs
        group by settlement_date
    ) as per_date
),

missing_on_matched_days as (
    select
        count(*) as half_hours,
        count(distinct window_inputs.settlement_date) as days
    from window_inputs
    inner join matched_days
        on window_inputs.settlement_date = matched_days.settlement_date
    where not window_inputs.has_all_three
),

-- A publish day missing from the back-fill leaves a half-hour paired with an
-- older forecast or a later outturn issue; these rows show how often.
demand_issue_ages as (
    select
        count(*) filter (
            where window_inputs.forecast_age_minutes > {{ stale_forecast_minutes }}
        ) as stale_forecast_half_hours,
        max(window_inputs.forecast_age_minutes) as oldest_forecast_minutes,
        count(*) filter (
            where window_inputs.outturn_delay_minutes > {{ late_outturn_minutes }}
        ) as late_outturn_half_hours,
        max(window_inputs.outturn_delay_minutes) as latest_outturn_minutes
    from window_inputs
    inner join matched_days
        on window_inputs.settlement_date = matched_days.settlement_date
    where window_inputs.has_all_three
),

coverage_rows as (
    select
        5 as section,
        'settlement dates in the window' as item,
        null::integer as quintile,
        half_hours,
        days,
        first_date,
        last_date,
        null::numeric as value,
        null::text as result
    from window_summary

    union all

    select 5, 'half-hours with error_api_final', null, carbon_half_hours, null, null, null, null, null
    from window_summary

    union all

    select 5, 'half-hours with demand_error_final_mw', null, demand_half_hours, null, null, null, null, null
    from window_summary

    union all

    select 5, 'half-hours with the cohort shortfall', null, shortfall_half_hours, null, null, null, null, null
    from window_summary

    union all

    select 5, 'half-hours with all three values', null, all_three_half_hours, with_all_three,
        null, null, null, null
    from window_summary
    cross join dates_with_each_input

    union all

    select 5, 'settlement dates without error_api_final in any half-hour', null, null, without_carbon,
        null, null, null, null
    from dates_with_each_input

    union all

    select 5, 'settlement dates without demand_error_final_mw in any half-hour', null, null, without_demand,
        null, null, null, null
    from dates_with_each_input

    union all

    select 5, 'settlement dates without the cohort shortfall in any half-hour', null, null, without_shortfall,
        null, null, null, null
    from dates_with_each_input

    union all

    select 5, 'settlement dates not matched', null, null, window_summary.days - outcome.days,
        null, null, null, null
    from window_summary
    cross join outcome

    union all

    select 5, 'half-hours on matched days without all three values', null, half_hours, days,
        null, null, null, null
    from missing_on_matched_days

    union all

    select 5, 'population half-hours whose last forecast was issued over {{ stale_forecast_minutes }} minutes before them (value: the oldest, minutes)',
        null, stale_forecast_half_hours, null, null, null, round(oldest_forecast_minutes::numeric, 6), null
    from demand_issue_ages

    union all

    select 5, 'population half-hours whose first outturn was published over {{ late_outturn_minutes }} minutes after they ended (value: the latest, minutes)',
        null, late_outturn_half_hours, null, null, null, round(latest_outturn_minutes::numeric, 6), null
    from demand_issue_ages
),

all_rows as (
    select * from population_rows
    union all
    select * from q8_rows
    union all
    select * from q28_rows
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
