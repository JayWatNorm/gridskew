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
-- of abs(error_final), ties ordered by a fixed pseudo-random number (amended
-- 26 October 2026, before any read; see below); "supported" if the top
-- quintile's mean is at least 25% above the bottom quintile's, otherwise "not
-- supported". If the bottom quintile's mean is 0 or less, both means and
-- their difference are reported and the ratio test and its interval are not
-- applicable. A 90% day-block bootstrap interval (1,000 draws) for the ratio
-- is reported beside the verdict and does not change it; a draw whose bottom
-- mean is not positive forms no ratio, and with fewer than 900 such ratios
-- the interval is unavailable. Read once, on the first day the population
-- reaches 90 matched days, at least three slot polls are stored after the
-- cutoff (so a forecast stall running past it is settled) and at least 8
-- days have passed since it (so outturn has settled). Amended 25 October 2026, before any read: the
-- quintiles and the bootstrap use only half-hours whose final forecast was
-- made at most 30 minutes ahead, the same "final" as Q1's, because during a
-- forecast stall the last counted forecast can be hours old. Matched days
-- are still counted at any final horizon, so a stall does not drop a whole
-- day. The result therefore describes half-hours with a fresh final
-- forecast, not every half-hour; the half-hours and matched days left out
-- are counted in section 3, and the bootstrap resamples only matched days
-- that hold at least one such half-hour.
-- Amended 7 November 2026, before any read: settlement runs. A settlement
-- date is eligible when every determinable active cohort unit-period on it
-- takes its metered value from the three-week run (SF) or a later run at the
-- read. Dates still wholly or partly on the one-week run (II) are left out
-- and listed in section 3 with their half-hours; the dates among them held
-- back by unit-periods with no SF or later row, while the date holds one,
-- are counted on their own line. Matched days and the gate count eligible
-- dates only, so analysis_read_until is the first date on which they reach
-- 90.
--
-- How the query reads it. The read passes shortfall_error_capture_cutoff,
-- its own time with an explicit UTC offset. Every decision that rests on
-- B1610 reads only the rows stored at or before it, ranked and mapped to the
-- registry as the period model and its mapped view do: whether a unit is
-- active on metered output, whether a unit-period has a metered value mapped
-- to the unit, its run (the latest held) and that value. A unit-period is
-- read when it has an expected energy (the PN side, from the shortfall
-- table) and such a held value; its unexplained shortfall is the expected
-- energy minus that value. So a rerun with the same cutoff reads the same
-- meter data, whatever B1610 arrived later. A unit-period with an expected
-- energy and no held value is left out and counted. "The date holds one"
-- means at least one such unit-period on it is on SF or later. Eligibility
-- is judged on the unit-periods the measure reads, on the settlement dates
-- from the window's first to analysis_read_until; the dates left out are
-- those with both values in at least one half-hour, listed with those
-- half-hours. Section 3 also gives the B1610 rows read, the shortfall
-- table's latest capture and the date the gate is reached. Without the
-- cutoff the query reads rows stored up to its own time and returns counts
-- and no result. Forecast and outturn captures are not limited by it: a
-- rerun limits them by hand, as before.
--
-- A half-hour has the cohort's unexplained shortfall when at least one
-- determinable cohort unit-period holds a value (Q5's population: with or
-- without a PN; before ABSVD). Quintiles are ntile(5), so the lower quintiles
-- take one extra half-hour when the count is not a multiple of 5. Errors are
-- whole gCO2/kWh, so many half-hours can share one; ordering a tie by date
-- would fill the bottom quintile with the earliest of them. Ties are instead
-- ordered by quintile_tie_order (md5 of the seed, the draw and pick, and the
-- half-hour), then by period start; section 2 reports how many half-hours
-- share the bottom quintile's largest error. Each
-- bootstrap draw takes as many eligible days as there are, with replacement;
-- its picks come from md5 of a fixed seed, the draw and the pick, so every
-- run gives the same interval. The interval resamples whole days for the
-- correlation within a day; correlation between days is not modelled. Until
-- the population is reached it returns counts and no result.
--
-- Amended 31 October 2026, before any read. Nothing in the registered test
-- changes; informed by Q28's read (ratio 1.05, 90% interval 1.03 to 1.08);
-- the test and its threshold do not change.
-- - Reported first: the ratio of the top quintile's mean to the bottom's,
--   with its interval; the verdict beside it (section 1, in position order).
--   With a bottom mean of 0 or less the edge rule is unchanged: both means
--   and their difference, no ratio and no interval.
-- - Beside it: by weekday of the settlement date, Monday to Sunday, the
--   matched-day half-hours kept and those left out for a final forecast made
--   more than 30 minutes ahead (counts); and the half-hours sharing the top
--   quintile's smallest error, as for the bottom.
-- - Units: only active cohort units count (active_cohort_units): those with a
--   PN or metered output above zero in at least one half-hour of the window,
--   from the first half-hour with an error_final to analysis_read_until.
--   Interconnectors (fuel type beginning INT, so INTELE by assumption) are
--   left out. The units by status, the active units with no PN unit-period
--   (they add nothing to the sums), and the idle unit-periods (determinable,
--   PN 0 and metered 0) among the active units' determinable unit-periods
--   behind the population half-hours are reported in coverage.

{% set days_needed = var('shortfall_error_matched_days_needed') %}
{% set read_until = analysis_read_until() %}
{% set final_horizon_limit_seconds = (var('forecast_trajectory_final_horizon_hours') * 3600) | int %}
{% set matched_day_min_half_hours = 46 %}
{% set bootstrap_draws = 1000 %}
{% set bootstrap_min_usable_draws = var('shortfall_error_bootstrap_min_usable_draws') %}
{% set bootstrap_seed = 20261012 %}
{% if read_until is not none %}
    {% set cohort_window_end = "timestamptz '" ~ read_until ~ "'" %}
{% else %}
    {% set cohort_window_end = none %}
{% endif %}
{% set capture_cutoff = utc_instant_var('shortfall_error_capture_cutoff') %}
{% set stored_until %}{% if capture_cutoff is none %}now(){% else %}timestamptz '{{ capture_cutoff }}'{% endif %}{% endset %}
{% set capture_cutoff_recorded = 'false' if capture_cutoff is none else 'true' %}
{% set window_start = '(select min(period_start) from errors)' %}

with errors as (
    select
        period_start,
        error_final,
        final_horizon_seconds <= {{ final_horizon_limit_seconds }} as is_fresh
    from {{ ref('int_carbon_error_by_period') }}
    where error_final is not null
{% if read_until is not none %}
        and period_start <= timestamptz '{{ read_until }}'
{% endif %}
),

errors_left_out as (
    select count(*) as half_hours
    from errors
    where not is_fresh
),

-- The B1610 rows of cohort Elexon IDs stored by the capture cutoff, on the
-- settlement dates around the window, each ranked as the period model ranks
-- them: first stored (ties by run order) and latest run.
held_b1610_rows as (
    select
        b1610.bm_unit,
        b1610.settlement_date,
        b1610.settlement_period,
        b1610.period_start_utc,
        b1610.national_grid_bm_unit_id,
        b1610.quantity,
        run_codes.run_order,
        row_number() over (
            partition by b1610.bm_unit, b1610.settlement_date, b1610.settlement_period
            order by b1610.retrieved_at, run_codes.run_order
        ) as first_rank,
        row_number() over (
            partition by b1610.bm_unit, b1610.settlement_date, b1610.settlement_period
            order by run_codes.run_order desc
        ) as latest_rank
    from {{ ref('stg_elexon__b1610') }} as b1610
    inner join {{ ref('elexon_settlement_run_codes') }} as run_codes
        on b1610.settlement_run_type = run_codes.settlement_run_code
    where b1610.retrieved_at <= {{ stored_until }}
        and b1610.bm_unit in (
            select elexon_bm_unit
            from {{ ref('int_elexon__bm_unit_cohort') }}
            where elexon_bm_unit is not null
        )
        and b1610.settlement_date >= ({{ window_start }} at time zone 'Europe/London')::date - 1
{% if read_until is not none %}
        and b1610.settlement_date <= (timestamptz '{{ read_until }}' at time zone 'Europe/London')::date + 1
{% endif %}
),

-- Each of those unit-periods as int_elexon__b1610_period_mapped gives it, but
-- from the rows held at the cutoff only: the first stored and the latest run,
-- and the mapping to the registry. Mapped needs exactly one registry unit for
-- the Elexon ID and no National Grid ID on either row that differs from it
-- (letter case ignored), as in that view; it cannot read the cutoff itself.
held_metered_periods as (
    select
        periods.bm_unit,
        periods.settlement_date,
        periods.settlement_period,
        periods.period_start_utc,
        periods.first_quantity_mwh,
        periods.latest_quantity_mwh,
        periods.latest_run_order,
        case
            when registry.candidate_count is null then 'unmapped'
            when registry.candidate_count > 1 then 'ambiguous'
            when lower(periods.first_source_national_grid_bm_unit_id)
                <> lower(registry.national_grid_bm_unit)
                or lower(periods.latest_source_national_grid_bm_unit_id)
                <> lower(registry.national_grid_bm_unit)
                then 'conflict'
            else 'mapped'
        end as mapping_status,
        registry.national_grid_bm_unit as registry_national_grid_bm_unit
    from (
        select
            bm_unit,
            settlement_date,
            settlement_period,
            min(period_start_utc) as period_start_utc,
            max(quantity) filter (where first_rank = 1) as first_quantity_mwh,
            max(national_grid_bm_unit_id) filter (where first_rank = 1)
                as first_source_national_grid_bm_unit_id,
            max(quantity) filter (where latest_rank = 1) as latest_quantity_mwh,
            max(run_order) filter (where latest_rank = 1) as latest_run_order,
            max(national_grid_bm_unit_id) filter (where latest_rank = 1)
                as latest_source_national_grid_bm_unit_id
        from held_b1610_rows
        group by bm_unit, settlement_date, settlement_period
    ) as periods
    left join (
        select
            elexon_bm_unit,
            count(*) as candidate_count,
            min(national_grid_bm_unit) as national_grid_bm_unit
        from {{ ref('int_elexon__bm_units_current') }}
        where elexon_bm_unit is not null
        group by elexon_bm_unit
    ) as registry
        on periods.bm_unit = registry.elexon_bm_unit
),

-- The same in the shape active_cohort_units reads: the mapped unit only when
-- mapped.
held_metered_mapped as (
    select
        bm_unit,
        settlement_date,
        settlement_period,
        period_start_utc,
        first_quantity_mwh,
        latest_quantity_mwh,
        latest_run_order,
        mapping_status,
        case when mapping_status = 'mapped'
            then registry_national_grid_bm_unit
        end as mapped_national_grid_bm_unit
    from held_metered_periods
),

-- Every cohort unit with its status in the window from the first half-hour
-- with an error_final to the cutoff: PN from the period view, metered output
-- from the rows held at the capture cutoff.
cohort_units as (
    select
        national_grid_bm_unit,
        fuel_type,
        cohort_status,
        has_row_in_window
    from {{ active_cohort_units(window_start, cohort_window_end, 'held_metered_mapped') }}
        as units
),

-- The active cohort unit-periods the measure can read, on the settlement
-- dates from the window's first: an expected energy (the PN side of
-- determinability, as the shortfall table judges it).
read_unit_periods as (
    select
        shortfall.national_grid_bm_unit,
        shortfall.elexon_bm_unit,
        shortfall.settlement_date,
        shortfall.settlement_period,
        shortfall.period_start_utc,
        shortfall.expected_mwh,
        shortfall.pn_mwh
    from {{ ref('int_shortfall_by_unit_period') }} as shortfall
    inner join cohort_units as cohort
        on shortfall.national_grid_bm_unit = cohort.national_grid_bm_unit
        and cohort.cohort_status = 'active'
    where shortfall.expected_mwh is not null
        and shortfall.period_start_utc is not null
        and shortfall.settlement_date >= ({{ window_start }} at time zone 'Europe/London')::date
{% if read_until is not none %}
        and shortfall.period_start_utc <= timestamptz '{{ read_until }}'
{% endif %}
),

-- The metered side of determinability, at the cutoff: a held unit-period of
-- the unit's Elexon ID mapped to the unit itself, as the shortfall table
-- requires of its metered row. Its latest run gives the metered value.
held_runs as (
    select
        read_unit_periods.settlement_date,
        read_unit_periods.period_start_utc,
        read_unit_periods.pn_mwh,
        held.latest_quantity_mwh as metered_mwh,
        round(read_unit_periods.expected_mwh - held.latest_quantity_mwh, 6)
            as unexplained_shortfall_mwh,
        held.latest_run_order >= (
            select sf.run_order
            from {{ ref('elexon_settlement_run_codes') }} as sf
            where sf.settlement_run_code = 'SF'
        ) as is_sf_or_later
    from read_unit_periods
    inner join held_metered_mapped as held
        on held.bm_unit = read_unit_periods.elexon_bm_unit
        and held.settlement_date = read_unit_periods.settlement_date
        and held.settlement_period = read_unit_periods.settlement_period
        and held.mapping_status = 'mapped'
        and held.mapped_national_grid_bm_unit = read_unit_periods.national_grid_bm_unit
),

unit_periods_without_held_run as (
    select (select count(*) from read_unit_periods) - (select count(*) from held_runs)
        as unit_periods
),

cohort_shortfall as (
    select
        settlement_date,
        period_start_utc as period_start,
        sum(greatest(unexplained_shortfall_mwh, 0)) as positive_shortfall_mwh,
        count(*) as unit_periods,
        count(*) filter (where not is_sf_or_later) as unit_periods_on_ii
    from held_runs
    group by settlement_date, period_start_utc
),

-- Each settlement date's unit-periods on II and on SF or later at the read.
date_runs as (
    select
        settlement_date,
        sum(unit_periods_on_ii) as unit_periods_on_ii,
        sum(unit_periods) - sum(unit_periods_on_ii) as unit_periods_sf_or_later
    from cohort_shortfall
    group by settlement_date
),

both_values as (
    select
        cohort_shortfall.settlement_date,
        cohort_shortfall.period_start,
        errors.error_final,
        errors.is_fresh,
        cohort_shortfall.positive_shortfall_mwh,
        cohort_shortfall.unit_periods
    from cohort_shortfall
    inner join errors
        on cohort_shortfall.period_start = errors.period_start
),

-- Dates with both values in at least one half-hour, each eligible or left
-- out at the read.
both_values_dates as (
    select
        both_values.settlement_date,
        count(*) as half_hours,
        date_runs.unit_periods_on_ii,
        date_runs.unit_periods_on_ii = 0 as is_eligible,
        date_runs.unit_periods_sf_or_later > 0 as holds_sf_or_later
    from both_values
    inner join date_runs
        on both_values.settlement_date = date_runs.settlement_date
    group by
        both_values.settlement_date,
        date_runs.unit_periods_on_ii,
        date_runs.unit_periods_sf_or_later
),

matched_days as (
    select settlement_date
    from both_values_dates
    where is_eligible
        and half_hours >= {{ matched_day_min_half_hours }}
),

-- Matched days holding at least one half-hour with a fresh final forecast:
-- the days the bootstrap resamples.
bootstrap_days as (
    select
        both_values.settlement_date,
        row_number() over (order by both_values.settlement_date) as day_number
    from both_values
    inner join matched_days
        on both_values.settlement_date = matched_days.settlement_date
    where both_values.is_fresh
    group by both_values.settlement_date
),

population as (
    select
        both_values.settlement_date,
        both_values.period_start,
        both_values.error_final,
        both_values.positive_shortfall_mwh,
        both_values.unit_periods,
        bootstrap_days.day_number
    from both_values
    inner join bootstrap_days
        on both_values.settlement_date = bootstrap_days.settlement_date
    where both_values.is_fresh
),

-- The eligible matched day on which the gate is reached, if it is.
gate_day as (
    select settlement_date
    from (
        select
            settlement_date,
            row_number() over (order by settlement_date) as day_order
        from matched_days
    ) as ordered
    where day_order = {{ days_needed }}
),

-- The result needs the matched days, the slot polls after the cutoff and a
-- recorded capture cutoff.
gate as (
    select
        count(*) as matched_days,
        (select count(*) from bootstrap_days) as bootstrap_days,
        count(*) >= {{ days_needed }} as is_met,
        count(*) >= {{ days_needed }}
            and {{ forecast_look_ahead_reached(read_until) }}
            and {{ capture_cutoff_recorded }} as is_reached
    from matched_days
),

quintiles as (
    select
        period_start,
        error_final,
        positive_shortfall_mwh,
        ntile(5) over (
            order by abs(error_final), {{ quintile_tie_order(bootstrap_seed, 0, 0, 'period_start') }}, period_start
        ) as quintile
    from population
),

-- How many half-hours share the bottom quintile's largest error, and how
-- many of them the tie order put in the bottom quintile.
bottom_boundary_ties as (
    select
        count(*) as half_hours,
        count(*) filter (where quintile = 1) as in_bottom,
        max(abs(error_final)) as boundary_abs_error_final
    from quintiles
    where abs(error_final) = (select max(abs(error_final)) from quintiles where quintile = 1)
),

-- The same at the top: the half-hours sharing the top quintile's smallest
-- error, and how many of them the tie order put in the top quintile.
top_boundary_ties as (
    select
        count(*) as half_hours,
        count(*) filter (where quintile = 5) as in_top,
        min(abs(error_final)) as boundary_abs_error_final
    from quintiles
    where abs(error_final) = (select min(abs(error_final)) from quintiles where quintile = 5)
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
        {{ bootstrap_date_number(bootstrap_seed, 'draws.draw', 'picked.pick', 'gate.bootstrap_days') }}
            as day_number
    from gate
    cross join observed
    cross join generate_series(1, {{ bootstrap_draws }}) as draws (draw)
    cross join generate_series(1, gate.bootstrap_days) as picked (pick)
    where gate.is_reached
        and observed.bottom_sum > 0
),

resampled_quintiles as (
    select
        picks.draw,
        population.positive_shortfall_mwh,
        ntile(5) over (
            partition by picks.draw
            order by
                abs(population.error_final),
                {{ quintile_tie_order(bootstrap_seed, 'picks.draw', 'picks.pick', 'population.period_start') }},
                population.period_start,
                picks.pick
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

-- Section 1 leads with the ratio and its interval; the verdict stands beside
-- them, then the means and the population.
verdict_rows as (
    select
        1 as section,
        1 as position,
        'ratio, top over bottom' as item,
        null::integer as quintile,
        null::bigint as half_hours,
        null::bigint as days,
        null::date as first_date,
        null::date as last_date,
        case when is_reached and bottom_mean > 0 then round(top_mean / bottom_mean, 6) end as value,
        null::text as result
    from outcome

    union all

    select 1, 2, 'bootstrap 90% interval, low', null, null, null, null, null,
        case when interval_status = 'available' then round(interval_low::numeric, 6) end, null
    from outcome

    union all

    select 1, 3, 'bootstrap 90% interval, high', null, null, null, null, null,
        case when interval_status = 'available' then round(interval_high::numeric, 6) end, null
    from outcome

    union all

    select 1, 4, 'bootstrap draws with a ratio ({{ bootstrap_min_usable_draws }} of {{ bootstrap_draws }} needed)',
        null, null, null, null, null,
        case when interval_status in ('available', 'unavailable') then usable_draws end, interval_status
    from outcome

    union all

    select 1, 5, 'verdict (supported at a ratio of 1.25 or more)', null, null, null, null, null,
        null, verdict
    from outcome

    union all

    select 1, 6, 'bottom quintile mean, MWh', null, null, null, null, null,
        case when is_reached then round(bottom_mean, 6) end, null
    from outcome

    union all

    select 1, 7, 'top quintile mean, MWh', null, null, null, null, null,
        case when is_reached then round(top_mean, 6) end, null
    from outcome

    union all

    select 1, 8, 'difference, top minus bottom, MWh', null, null, null, null, null,
        case when is_reached then round(top_mean - bottom_mean, 6) end, null
    from outcome

    union all

    select
        1,
        9,
        'population: matched days ({{ days_needed }} needed)',
        null,
        population_summary.half_hours,
        outcome.matched_days,
        population_summary.first_date,
        population_summary.last_date,
        null,
        case when outcome.is_reached then 'reached' else 'not reached' end
    from outcome
    cross join population_summary
),

quintile_rows as (
    select
        2 as section,
        null::integer as position,
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

    union all

    select 2, null, 'half-hours sharing the bottom quintile''s largest abs(error_final) (value: that error)', null,
        case when outcome.is_reached then bottom_boundary_ties.half_hours end, null, null, null,
        case when outcome.is_reached then bottom_boundary_ties.boundary_abs_error_final end,
        case when outcome.is_reached then 'in the bottom quintile: ' || bottom_boundary_ties.in_bottom end
    from bottom_boundary_ties
    cross join outcome

    union all

    select 2, null, 'half-hours sharing the top quintile''s smallest abs(error_final) (value: that error)', null,
        case when outcome.is_reached then top_boundary_ties.half_hours end, null, null, null,
        case when outcome.is_reached then top_boundary_ties.boundary_abs_error_final end,
        case when outcome.is_reached then 'in the top quintile: ' || top_boundary_ties.in_top end
    from top_boundary_ties
    cross join outcome
),

error_dates as (
    select distinct (period_start at time zone 'Europe/London')::date as settlement_date
    from errors
),

coverage_rows as (
    select
        3 as section,
        null::integer as position,
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

    select 3, null, 'half-hours with error_final left out: final forecast over '
        || {{ final_horizon_limit_seconds // 60 }} || ' minutes ahead', null, half_hours,
        null, null, null, null, null
    from errors_left_out

    union all

    select 3, null, 'matched days with no half-hour whose final forecast is within '
        || {{ final_horizon_limit_seconds // 60 }} || ' minutes', null, null,
        gate.matched_days - gate.bootstrap_days, null, null, null, null
    from gate

    union all

    select 3, null, 'settlement dates with the cohort measure', null, null,
        count(distinct settlement_date), min(settlement_date), max(settlement_date), null, null
    from cohort_shortfall

    union all

    select 3, null, 'settlement dates with both values in any half-hour', null, null,
        count(distinct settlement_date), min(settlement_date), max(settlement_date), null, null
    from both_values

    union all

    select 3, null, 'on matched days: cohort measure without error_final', null, count(*),
        count(distinct cohort_shortfall.settlement_date), null, null, null, null
    from cohort_shortfall
    inner join matched_days
        on cohort_shortfall.settlement_date = matched_days.settlement_date
    left join errors
        on cohort_shortfall.period_start = errors.period_start
    where errors.period_start is null

    union all

    select 3, null, 'on matched days: error_final without the cohort measure', null, count(*),
        count(distinct matched_days.settlement_date), null, null, null, null
    from errors
    inner join matched_days
        on (errors.period_start at time zone 'Europe/London')::date = matched_days.settlement_date
    left join cohort_shortfall
        on errors.period_start = cohort_shortfall.period_start
    where cohort_shortfall.period_start is null

    union all

    select 3, null, 'cohort unit-periods per population half-hour: min', null, count(*), null, null, null,
        min(unit_periods), null
    from population

    union all

    select 3, null, 'cohort unit-periods per population half-hour: median', null, count(*), null, null, null,
        percentile_cont(0.5) within group (order by unit_periods)::numeric, null
    from population

    union all

    select 3, null, 'cohort unit-periods per population half-hour: max', null, count(*), null, null, null,
        max(unit_periods), null
    from population
),

-- Settlement runs at the read: the cutoff, the eligible dates, the gate on
-- them, the dates left out with their half-hours and the unit-periods with
-- no row stored by the cutoff. Counts and dates only.
settlement_run_rows as (
    select
        3 as section,
        null::integer as position,
        'B1610 rows read: stored at or before shortfall_error_capture_cutoff (result: that cutoff)'
            as item,
        null::integer as quintile,
        null::bigint as half_hours,
        null::bigint as days,
        null::date as first_date,
        null::date as last_date,
        null::numeric as value,
{% if capture_cutoff is none %}
        'not recorded: rows stored up to the query''s own time; no result' as result
{% else %}
        '{{ capture_cutoff }}' as result
{% endif %}

    union all

    select 3, null,
        'settlement dates with both values in any half-hour: eligible, every determinable active '
            || 'cohort unit-period on the three-week run (SF) or later',
        null, null, count(*), min(settlement_date), max(settlement_date), null, null
    from both_values_dates
    where is_eligible

    union all

    select 3, null, 'matched days on eligible dates ({{ days_needed }} needed)', null, null,
        matched_days, null, null, null,
        case when is_met then 'met' else 'not met' end
    from gate

    union all

    select 3, null,
        'settlement dates left out: a determinable active cohort unit-period on the one-week '
            || 'run (II) (value: those unit-periods)',
        null, coalesce(sum(half_hours), 0)::bigint, count(*), min(settlement_date), max(settlement_date),
        coalesce(sum(unit_periods_on_ii), 0), null
    from both_values_dates
    where not is_eligible

    union all

    select 3, null,
        'settlement dates left out: of those, held back by unit-periods with no SF or later row '
            || 'while the date holds one (value: those unit-periods)',
        null, coalesce(sum(half_hours), 0)::bigint, count(*), min(settlement_date), max(settlement_date),
        coalesce(sum(unit_periods_on_ii), 0), null
    from both_values_dates
    where not is_eligible
        and holds_sf_or_later

    union all

    select 3, null,
        'left out, on the one-week run (II): ' || to_char(settlement_date, 'YYYY-MM-DD')
            || ' (value: unit-periods on II)',
        null, half_hours, 1, settlement_date, settlement_date, unit_periods_on_ii,
        case
            when holds_sf_or_later then 'partly on II: the date holds SF or a later run'
            else 'wholly on II'
        end
    from both_values_dates
    where not is_eligible

    union all

    select 3, null,
        'active cohort unit-periods with an expected energy but no B1610 row mapped to the unit '
            || 'stored by the cutoff, left out (value: unit-periods)',
        null, null, null, null, null, unit_periods, null
    from unit_periods_without_held_run

    union all

    select 3, null,
        'B1610 rows read: stored by the cutoff, cohort Elexon IDs, the window''s settlement dates '
            || '(value: rows)',
        null, null, null, null, null, count(*), null
    from held_b1610_rows

    union all

    select 3, null,
        'shortfall table: its latest capture, max(last_captured_at), beside the cutoff (result: UTC)',
        null, null, null, null, null, null,
        to_char(max(last_captured_at) at time zone 'UTC', 'YYYY-MM-DD HH24:MI:SS') || '+00'
    from {{ ref('int_shortfall_by_unit_period') }}

    union all

    select 3, null,
        'eligible matched day number {{ days_needed }}: the date the gate is reached (first_date)',
        null, null, null, gate_day.settlement_date, null, null,
        case
            when gate_day.settlement_date is null then 'not reached'
{% if read_until is none %}
            else 'no analysis_read_until to compare'
{% else %}
            when gate_day.settlement_date
                = (timestamptz '{{ read_until }}' at time zone 'Europe/London')::date
                then 'the settlement date of analysis_read_until'
            else 'not the settlement date of analysis_read_until, '
                || to_char((timestamptz '{{ read_until }}' at time zone 'Europe/London')::date, 'YYYY-MM-DD')
{% endif %}
        end
    from (select 1) as one_row
    left join gate_day
        on true
),

listed_weekdays as (
    select
        weekday,
        weekday_name
    from (
        values
            (1, 'Monday'), (2, 'Tuesday'), (3, 'Wednesday'), (4, 'Thursday'),
            (5, 'Friday'), (6, 'Saturday'), (7, 'Sunday')
    ) as listed (weekday, weekday_name)
),

matched_day_half_hours as (
    select
        both_values.settlement_date,
        both_values.is_fresh
    from both_values
    inner join matched_days
        on both_values.settlement_date = matched_days.settlement_date
),

-- By ISO weekday of the settlement date, every weekday listed. Counts only.
stale_final_rows as (
    select
        4 as section,
        listed_weekdays.weekday as position,
        'matched-day half-hours kept, final forecast at most '
            || {{ final_horizon_limit_seconds // 60 }} || ' minutes ahead: settlement date on a '
            || listed_weekdays.weekday_name as item,
        null::integer as quintile,
        count(matched_day_half_hours.settlement_date) as half_hours,
        null::bigint as days,
        null::date as first_date,
        null::date as last_date,
        null::numeric as value,
        null::text as result
    from listed_weekdays
    left join matched_day_half_hours
        on extract(isodow from matched_day_half_hours.settlement_date) = listed_weekdays.weekday
        and matched_day_half_hours.is_fresh
    group by listed_weekdays.weekday, listed_weekdays.weekday_name

    union all

    select
        4,
        listed_weekdays.weekday,
        'matched-day half-hours left out, final forecast over '
            || {{ final_horizon_limit_seconds // 60 }} || ' minutes ahead: settlement date on a '
            || listed_weekdays.weekday_name,
        null,
        count(matched_day_half_hours.settlement_date),
        null,
        null,
        null,
        null,
        null
    from listed_weekdays
    left join matched_day_half_hours
        on extract(isodow from matched_day_half_hours.settlement_date) = listed_weekdays.weekday
        and not matched_day_half_hours.is_fresh
    group by listed_weekdays.weekday, listed_weekdays.weekday_name
),

-- The active units' determinable unit-periods behind the population
-- half-hours (those the measure sums, with a metered value held at the
-- capture cutoff), and the idle ones among them: PN 0 and that metered value
-- 0, both exact.
population_unit_periods as (
    select
        count(*) as determinable_unit_periods,
        count(*) filter (
            where held_runs.pn_mwh = 0
                and held_runs.metered_mwh = 0
        ) as idle_unit_periods
    from held_runs
    inner join population
        on held_runs.period_start_utc = population.period_start
),

-- Active units with no unit-period in the shortfall table (no PN) in the
-- window: active on metered output alone, they add nothing to the sums.
active_units_without_pn as (
    select count(*) as units
    from cohort_units as cohort
    where cohort.cohort_status = 'active'
        and not exists (
            select 1
            from {{ ref('int_shortfall_by_unit_period') }} as shortfall
            where shortfall.national_grid_bm_unit = cohort.national_grid_bm_unit
                and {{ active_cohort_window_test('shortfall', '(select min(period_start) from errors)', cohort_window_end) }}
        )
),

-- Counts of units and unit-periods, and the idle share, all shown before the
-- gate.
cohort_rows as (
    select
        5 as section,
        1 as position,
        'cohort units: active in the window (value: units)' as item,
        null::integer as quintile,
        null::bigint as half_hours,
        null::bigint as days,
        null::date as first_date,
        null::date as last_date,
        count(*) filter (where cohort_status = 'active')::numeric as value,
        null::text as result
    from cohort_units

    union all

    select 5, 2, 'cohort units: interconnectors, fuel type beginning INT, left out (value: units)',
        null, null, null, null, null,
        count(*) filter (where cohort_status = 'interconnector'), null
    from cohort_units

    union all

    select 5, 3,
        'cohort units: interconnectors with fuel type INTELE, an interconnector by assumption (value: units)',
        null, null, null, null, null,
        count(*) filter (where cohort_status = 'interconnector' and fuel_type = 'INTELE'), null
    from cohort_units

    union all

    select 5, 4, 'cohort units: inactive in the window, left out (value: units)',
        null, null, null, null, null,
        count(*) filter (where cohort_status = 'inactive'), null
    from cohort_units

    union all

    select 5, 5, 'cohort units: inactive with no row in the window (value: units)',
        null, null, null, null, null,
        count(*) filter (where cohort_status = 'inactive' and not has_row_in_window), null
    from cohort_units

    union all

    select 5, 6,
        'active units with no PN unit-period in the window, adding nothing to the sums (value: units)',
        null, null, null, null, null, units, null
    from active_units_without_pn

    union all

    select 5, 7,
        'idle unit-periods behind the population half-hours: determinable, PN 0 and metered 0 (value: unit-periods)',
        null, null, null, null, null, idle_unit_periods, null
    from population_unit_periods

    union all

    select 5, 8,
        'determinable unit-periods of active units behind the population half-hours (value: unit-periods)',
        null, null, null, null, null, determinable_unit_periods, null
    from population_unit_periods

    union all

    select 5, 9, 'idle share of those determinable unit-periods',
        null, null, null, null, null,
        round(
            population_unit_periods.idle_unit_periods::numeric
                / nullif(population_unit_periods.determinable_unit_periods, 0),
            6
        ),
        null
    from population_unit_periods
),

all_rows as (
    select section, position, item, quintile, half_hours, days, first_date, last_date, value, result
    from verdict_rows
    union all
    select section, position, item, quintile, half_hours, days, first_date, last_date, value, result
    from quintile_rows
    union all
    select section, position, item, quintile, half_hours, days, first_date, last_date, value, result
    from coverage_rows
    union all
    select section, position, item, quintile, half_hours, days, first_date, last_date, value, result
    from settlement_run_rows
    union all
    select section, position, item, quintile, half_hours, days, first_date, last_date, value, result
    from stale_final_rows
    union all
    select section, position, item, quintile, half_hours, days, first_date, last_date, value, result
    from cohort_rows
)

select
    section,
    position,
    item,
    quintile,
    half_hours,
    days,
    first_date,
    last_date,
    value,
    result
from all_rows
order by section, position, item, quintile
