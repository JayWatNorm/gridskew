{{ config(group='analysis') }}

-- Q30: is the size of the miss between wind outturn (curtailment added
-- back) and NESO's latest wind forecast associated with the size of the
-- error in the API's final carbon forecast, on 2025 history? Reading rule
-- (research questions, registered 27 October 2026, before any figure of it
-- was read):
--
-- Window: settlement dates q30_window_from to q30_window_until, both
-- included (22 August 2025 to 16 August 2026), half-hours from the
-- settlement calendar, clock-change dates with their 46 or 50. Carbon error:
-- error_api_final, the outturn minus the API's final forecast, taken as Q8
-- and Q29 take it from int_carbon_outturn__latest (int_carbon_error_by_period
-- holds only half-hours with an archived forecast, none in this window).
-- Wind: int_wind_error_by_period, which pairs each half-hour with the
-- latest WINDFOR issue strictly before it and that issue's point for the
-- hour (no fall-back to an older issue), the first FUELHH WIND outturn and
-- the cohort wind curtailment add-back.
--
-- Population: every window half-hour with error_api_final, a forecast, a
-- first outturn and a known add-back, so the primary and the sensitivity
-- stand on the same half-hours. No matched-day threshold. Section 5 counts
-- every left-out half-hour once, by the first reason it meets, in the order
-- listed.
--
-- Measures, describe only (no test, threshold or verdict; "associated
-- with", never "caused by"). The primary wind error is labelled "outturn
-- with cohort curtailment add-back", never a confirmed unconstrained miss.
-- Primary: rho_abs, Spearman of abs(wind error) with abs(error_api_final).
-- Secondary: rho_signed, Spearman of the signed pair (expected direction
-- negative), and quintiles of abs(wind error) with their n, range, mean
-- abs(error_api_final) and median error_api_final. Sensitivity: rho_abs and
-- rho_signed on the delivered-outturn wind error. Spearman uses average
-- ranks, and each rho is the Pearson correlation of the ranks.
--
-- Quintiles are ntile(5), the lower quintiles taking one extra half-hour
-- when the count is not a multiple of 5, ordered by abs(wind error), then
-- Q4's fixed pseudo-random key (quintile_tie_order), then period start. For
-- each quintile below the top, the half-hours sharing its largest
-- abs(wind error) are counted, with how many of them it holds.
--
-- Intervals, descriptive: a 90% percentile interval for every rho from a
-- day-block bootstrap of 1,000 draws over the settlement dates holding
-- population half-hours, each draw taking as many dates as there are, with
-- replacement, picked by md5 of the fixed seed q30_bootstrap_seed, the draw
-- and the pick. A draw where a measure is constant forms no rho for it; with
-- fewer than q30_bootstrap_min_usable_draws usable draws an interval is
-- unavailable. Resampling whole days keeps the correlation within a day.
--
-- Read once, after the build review and the counts-only coverage check,
-- with n, the window and the coverage beside every number.

{% set window_from = var('q30_window_from') %}
{% set window_until = var('q30_window_until') %}
{% set bootstrap_seed = var('q30_bootstrap_seed') %}
{% set bootstrap_draws = 1000 %}
{% set bootstrap_min_usable_draws = var('q30_bootstrap_min_usable_draws') %}
{% set short_date_half_hours = 46 %}
{% set primary_error = 'wind error, outturn with cohort curtailment add-back' %}
{% set delivered_error = 'wind error, delivered outturn' %}

with window_days as (
    select
        date '{{ window_from }}' + day_offset as settlement_date
    from generate_series(0, date '{{ window_until }}' - date '{{ window_from }}') as offsets (day_offset)
),

window_half_hours as (
    select
        window_days.settlement_date,
        {{ settlement_period_count('window_days.settlement_date') }} as date_periods,
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

wind_errors as (
    select
        start_time as period_start,
        outturn_mw,
        forecast_published_at,
        forecast_mw,
        curtailment_add_back_mw,
        wind_units_with_acceptance_and_null_deviation,
        wind_units_with_null_deviation,
        wind_error_mw,
        delivered_wind_error_mw
    from {{ ref('int_wind_error_by_period') }}
),

window_inputs as (
    select
        window_half_hours.settlement_date,
        window_half_hours.date_periods,
        window_half_hours.period_start,
        carbon_errors.error_api_final,
        wind_errors.outturn_mw,
        wind_errors.forecast_published_at,
        wind_errors.curtailment_add_back_mw,
        wind_errors.wind_units_with_acceptance_and_null_deviation,
        wind_errors.wind_units_with_null_deviation,
        wind_errors.wind_error_mw,
        wind_errors.delivered_wind_error_mw,
        case
            when carbon_errors.error_api_final is null then 1
            when wind_errors.outturn_mw is null then 2
            when wind_errors.forecast_published_at is null then 3
            when wind_errors.forecast_mw is null then 4
            when wind_errors.curtailment_add_back_mw is null then 5
        end as left_out_reason
    from window_half_hours
    left join carbon_errors
        on window_half_hours.period_start = carbon_errors.period_start
    left join wind_errors
        on window_half_hours.period_start = wind_errors.period_start
),

population_days as (
    select
        settlement_date,
        row_number() over (order by settlement_date) as day_number
    from window_inputs
    where left_out_reason is null
    group by settlement_date
),

population as (
    select
        window_inputs.settlement_date,
        window_inputs.date_periods,
        window_inputs.period_start,
        window_inputs.error_api_final as carbon_error,
        abs(window_inputs.error_api_final) as abs_carbon_error,
        window_inputs.wind_error_mw as wind_error,
        abs(window_inputs.wind_error_mw) as abs_wind_error,
        window_inputs.delivered_wind_error_mw as delivered_error,
        abs(window_inputs.delivered_wind_error_mw) as abs_delivered_error,
        window_inputs.curtailment_add_back_mw,
        window_inputs.period_start - window_inputs.forecast_published_at as forecast_age,
        population_days.day_number
    from window_inputs
    inner join population_days
        on window_inputs.settlement_date = population_days.settlement_date
    where window_inputs.left_out_reason is null
),

picks as (
    select
        draws.draw,
        picked.pick,
        1 + mod(
            ('x' || substr(md5('{{ bootstrap_seed }}:' || draws.draw || ':' || picked.pick), 1, 8))::bit(32)::bigint,
            day_count.days
        ) as day_number
    from (select count(*) as days from population_days) as day_count
    cross join generate_series(1, {{ bootstrap_draws }}) as draws (draw)
    cross join generate_series(1, day_count.days) as picked (pick)
),

-- Draw 0 is the population itself; draws 1 to 1,000 resample it by day.
samples as (
    select
        0 as draw,
        carbon_error,
        abs_carbon_error,
        wind_error,
        abs_wind_error,
        delivered_error,
        abs_delivered_error
    from population

    union all

    select
        picks.draw,
        population.carbon_error,
        population.abs_carbon_error,
        population.wind_error,
        population.abs_wind_error,
        population.delivered_error,
        population.abs_delivered_error
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
        (rank() over by_carbon + count(*) over by_carbon) / 2.0 as carbon_rank,
        (rank() over by_abs_carbon + count(*) over by_abs_carbon) / 2.0 as abs_carbon_rank,
        (rank() over by_wind + count(*) over by_wind) / 2.0 as wind_rank,
        (rank() over by_abs_wind + count(*) over by_abs_wind) / 2.0 as abs_wind_rank,
        (rank() over by_delivered + count(*) over by_delivered) / 2.0 as delivered_rank,
        (rank() over by_abs_delivered + count(*) over by_abs_delivered) / 2.0 as abs_delivered_rank
    from samples
    window
        by_carbon as (partition by draw order by carbon_error),
        by_abs_carbon as (partition by draw order by abs_carbon_error),
        by_wind as (partition by draw order by wind_error),
        by_abs_wind as (partition by draw order by abs_wind_error),
        by_delivered as (partition by draw order by delivered_error),
        by_abs_delivered as (partition by draw order by abs_delivered_error)
),

sample_results as (
    select
        draw,
        count(*) as half_hours,
        corr(abs_wind_rank, abs_carbon_rank) as rho_abs,
        corr(wind_rank, carbon_rank) as rho_signed,
        corr(abs_delivered_rank, abs_carbon_rank) as delivered_rho_abs,
        corr(delivered_rank, carbon_rank) as delivered_rho_signed
    from sample_ranks
    group by draw
),

measures (measure, section, name, description) as (
    values
        (
            'rho_abs', 2, 'rho_abs (primary)',
            'abs({{ primary_error }}) with abs(error_api_final)'
        ),
        (
            'rho_signed', 3, 'rho_signed (secondary)',
            '{{ primary_error }}, with error_api_final; expected direction negative'
        ),
        (
            'delivered_rho_abs', 4, 'rho_abs (sensitivity)',
            'abs({{ delivered_error }}) with abs(error_api_final)'
        ),
        (
            'delivered_rho_signed', 4, 'rho_signed (sensitivity)',
            '{{ delivered_error }}, with error_api_final'
        )
),

sample_measures as (
    select draw, 'rho_abs' as measure, rho_abs as rho
    from sample_results

    union all

    select draw, 'rho_signed', rho_signed
    from sample_results

    union all

    select draw, 'delivered_rho_abs', delivered_rho_abs
    from sample_results

    union all

    select draw, 'delivered_rho_signed', delivered_rho_signed
    from sample_results
),

measure_bootstrap as (
    select
        measure,
        count(rho) filter (where draw > 0) as usable_draws,
        percentile_cont(0.05) within group (order by rho) filter (where draw > 0) as interval_low,
        percentile_cont(0.95) within group (order by rho) filter (where draw > 0) as interval_high
    from sample_measures
    group by measure
),

population_summary as (
    select
        count(*) as half_hours,
        count(distinct settlement_date) as days,
        min(settlement_date) as first_date,
        max(settlement_date) as last_date
    from population
),

measure_outcomes as (
    select
        measures.section,
        measures.name,
        measures.description,
        population_summary.half_hours,
        observed.rho,
        case
            when observed.rho is null then 'not applicable'
            when measure_bootstrap.usable_draws >= {{ bootstrap_min_usable_draws }} then 'available'
            else 'unavailable'
        end as interval_status,
        measure_bootstrap.usable_draws,
        measure_bootstrap.interval_low,
        measure_bootstrap.interval_high
    from measures
    cross join population_summary
    left join sample_measures as observed
        on measures.measure = observed.measure
        and observed.draw = 0
    left join measure_bootstrap
        on measures.measure = measure_bootstrap.measure
),

population_rows as (
    select
        1 as section,
        'population: window half-hours with error_api_final, a forecast, a first outturn and a known add-back' as item,
        null::integer as quintile,
        half_hours,
        days,
        first_date,
        last_date,
        null::numeric as value,
        null::text as result
    from population_summary

    union all

    select 1, 'reading: describe only, no test, threshold or verdict; associated with, never caused by',
        null, null, null, null, null, null,
        'stated expectation, not a test: abs(rho) under about 0.2'
),

measure_rows as (
    select
        section,
        name || ': ' || description as item,
        null::integer as quintile,
        half_hours,
        null::bigint as days,
        null::date as first_date,
        null::date as last_date,
        round(rho::numeric, 6) as value,
        null::text as result
    from measure_outcomes

    union all

    select section, name || ': bootstrap draws with a value ({{ bootstrap_min_usable_draws }} of {{ bootstrap_draws }} needed)',
        null, null, null, null, null,
        case when interval_status <> 'not applicable' then usable_draws end, interval_status
    from measure_outcomes

    union all

    select section, name || ': bootstrap 90% interval, low', null, null, null, null, null,
        case when interval_status = 'available' then round(interval_low::numeric, 6) end, null
    from measure_outcomes

    union all

    select section, name || ': bootstrap 90% interval, high', null, null, null, null, null,
        case when interval_status = 'available' then round(interval_high::numeric, 6) end, null
    from measure_outcomes
),

quintiles as (
    select
        abs_wind_error,
        abs_carbon_error,
        carbon_error,
        ntile(5) over (
            order by
                abs_wind_error,
                {{ quintile_tie_order(bootstrap_seed, '0', '0', 'period_start') }},
                period_start
        ) as quintile
    from population
),

quintile_summary as (
    select
        quintile,
        count(*) as half_hours,
        min(abs_wind_error) as min_abs_wind_error,
        max(abs_wind_error) as max_abs_wind_error,
        avg(abs_carbon_error) as mean_abs_carbon_error,
        percentile_cont(0.5) within group (order by carbon_error) as median_carbon_error
    from quintiles
    group by quintile
),

quintile_edges as (
    select
        quintile_summary.quintile,
        quintile_summary.max_abs_wind_error as edge_abs_wind_error,
        count(*) as half_hours,
        count(*) filter (where quintiles.quintile = quintile_summary.quintile) as in_quintile
    from quintile_summary
    inner join quintiles
        on quintiles.abs_wind_error = quintile_summary.max_abs_wind_error
    where quintile_summary.quintile < 5
    group by quintile_summary.quintile, quintile_summary.max_abs_wind_error
),

quintile_rows as (
    select
        3 as section,
        'quintile of abs({{ primary_error }}): mean abs(error_api_final)' as item,
        listed.quintile,
        quintile_summary.half_hours,
        null::bigint as days,
        null::date as first_date,
        null::date as last_date,
        round(quintile_summary.mean_abs_carbon_error, 6) as value,
        'abs(wind error) ' || trim_scale(quintile_summary.min_abs_wind_error)
            || ' to ' || trim_scale(quintile_summary.max_abs_wind_error) || ' MW' as result
    from generate_series(1, 5) as listed (quintile)
    left join quintile_summary
        on listed.quintile = quintile_summary.quintile

    union all

    select 3, 'quintile of abs({{ primary_error }}): median error_api_final', listed.quintile,
        quintile_summary.half_hours, null, null, null,
        round(quintile_summary.median_carbon_error::numeric, 6), null
    from generate_series(1, 5) as listed (quintile)
    left join quintile_summary
        on listed.quintile = quintile_summary.quintile

    union all

    select 3, 'half-hours sharing the quintile''s largest abs(wind error) (value: that error, MW)', quintile,
        half_hours, null, null, null, trim_scale(edge_abs_wind_error),
        'in this quintile: ' || in_quintile
    from quintile_edges
),

window_summary as (
    select
        count(*) as half_hours,
        count(distinct settlement_date) as days,
        min(settlement_date) as first_date,
        max(settlement_date) as last_date,
        coalesce(sum(wind_units_with_acceptance_and_null_deviation), 0) as unit_periods_with_acceptance_and_null_deviation,
        coalesce(sum(wind_units_with_null_deviation), 0) as unit_periods_with_null_deviation
    from window_inputs
),

left_out_reasons (left_out_reason, reason) as (
    values
        (1, 'no error_api_final'),
        (2, 'no first FUELHH wind outturn'),
        (3, 'no WINDFOR issue before the half-hour'),
        (4, 'the latest issue holds no point for the hour'),
        (5, 'add-back unknown: a wind unit with an acceptance has a null instructed deviation')
),

left_out_counts as (
    select
        left_out_reasons.left_out_reason,
        left_out_reasons.reason,
        count(window_inputs.period_start) as half_hours
    from left_out_reasons
    left join window_inputs
        on left_out_reasons.left_out_reason = window_inputs.left_out_reason
    group by left_out_reasons.left_out_reason, left_out_reasons.reason
),

population_by_date as (
    select
        window_days.settlement_date,
        {{ settlement_period_count('window_days.settlement_date') }} as date_periods,
        count(population.period_start) as half_hours
    from window_days
    left join population
        on window_days.settlement_date = population.settlement_date
    group by window_days.settlement_date
),

forecast_age_bands (band_order, band, above, up_to) as (
    values
        (1, 'at most 1 h', null::interval, interval '1 hour'),
        (2, 'over 1 h to 2 h', interval '1 hour', interval '2 hours'),
        (3, 'over 2 h to 4 h', interval '2 hours', interval '4 hours'),
        (4, 'over 4 h', interval '4 hours', null::interval)
),

forecast_age_counts as (
    select
        forecast_age_bands.band_order,
        forecast_age_bands.band,
        count(population.period_start) as half_hours
    from forecast_age_bands
    left join population
        on (forecast_age_bands.above is null or population.forecast_age > forecast_age_bands.above)
        and (forecast_age_bands.up_to is null or population.forecast_age <= forecast_age_bands.up_to)
    group by forecast_age_bands.band_order, forecast_age_bands.band
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

    select 5, 'left out, first reason ' || left_out_reason || ': ' || reason, null, half_hours,
        null, null, null, null, null
    from left_out_counts

    union all

    select 5, 'settlement dates with fewer than {{ short_date_half_hours }} population half-hours',
        null, coalesce(sum(half_hours), 0)::bigint, count(*), null, null, null, null
    from population_by_date
    where half_hours < {{ short_date_half_hours }}

    union all

    select 5, 'clock-change dates with ' || listed.periods || ' periods, held whole (value: their window half-hours)',
        null, coalesce(sum(population_by_date.half_hours), 0)::bigint, count(population_by_date.settlement_date),
        null, null, coalesce(sum(population_by_date.date_periods), 0), null
    from (values (46), (50)) as listed (periods)
    left join population_by_date
        on listed.periods = population_by_date.date_periods
    group by listed.periods

    union all

    select 5, 'population half-hours with a non-zero add-back', null, count(*), null, null, null, null, null
    from population
    where curtailment_add_back_mw <> 0

    union all

    select 5, 'wind unit-periods with an acceptance and a null instructed deviation, in window half-hours with an outturn (value: unit-periods)',
        null, null, null, null, null, unit_periods_with_acceptance_and_null_deviation, null
    from window_summary

    union all

    select 5, 'wind unit-periods with a null instructed deviation, with or without an acceptance, in window half-hours with an outturn (value: unit-periods)',
        null, null, null, null, null, unit_periods_with_null_deviation, null
    from window_summary

    union all

    select 5, 'population forecast age from the source stamp (stamp to half-hour start), band ' || band_order || ': ' || band, null,
        half_hours, null, null, null, null, null
    from forecast_age_counts
),

all_rows as (
    select * from population_rows
    union all
    select * from measure_rows
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
