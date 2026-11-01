{{ config(group='analysis') }}

-- Beside Q1's verdict (docs/carbon-forecast-trajectory.md), described, never
-- changing or withholding it. Amended 31 October 2026, before Q1's population
-- reached 2,880 and before any drift value was read. Read in the same look as
-- q1_forecast_drift_verdict, on its population and behind its gate (at
-- least forecast_drift_half_hours_needed complete half-hours from
-- forecast_drift_population_from to analysis_read_until, and three slot polls
-- stored after the cutoff). Before the gate opens it returns counts only.
--
-- - Mean drift with a 90% day-block bootstrap percentile interval: the
--   population's UTC dates, numbered 1 to n in ascending order, resampled
--   with replacement in 1,000 draws of n picks each, picked from md5 of the
--   seed q1-2026-10-31, the draw and the pick (bootstrap_date_number); the
--   interval is the 5th and 95th percentile_cont of the draws. Not called
--   pre-registered: an exploration on 11 September 2026 computed a near
--   preview.
-- - Correlation of successive revisions. For each population half-hour, its
--   counted polls made at or before its start, in descending horizon order
--   (the poll inside the half-hour is not used); each change is a poll's
--   forecast minus the previous counted poll's, so the changes sum to the
--   drift. Pearson's r over every pair of consecutive changes within one
--   half-hour, pooled over the population, with n pairs, n changes, the share
--   of changes equal to 0 (zeros stay in the pairs) and the same interval.
--   Not called pre-registered: a published analysis (gridcast) reports
--   negative values on overlapping dates. Under efficient revision it is near
--   zero; Q1 shows the usual direction of change, not whether the forecast is
--   efficient. A check counts the population half-hours whose changes do not
--   sum to their drift; it should be 0.
--
-- Each draw is worked from per-date sums (half-hours and summed drift; pairs
-- and the sums r needs), which equals pooling the resampled rows. A draw with
-- no value (r without variance) is left out and counted; with fewer than 900
-- of the 1,000 draws holding a value the interval is unavailable.

{% set half_hours_needed = var('forecast_drift_half_hours_needed') %}
{% set read_until = analysis_read_until() %}
{% set bootstrap_seed = 'q1-2026-10-31' %}
{% set bootstrap_draws = 1000 %}
{% set bootstrap_min_draws_with_value = 900 %}

with population as (
    select
        period_start,
        drift,
        (period_start at time zone 'UTC')::date as utc_date
    from {{ ref('int_carbon_error_by_period') }}
    where has_complete_trajectory
        and period_start >= timestamptz '{{ var("forecast_drift_population_from") }}'
{% if read_until is not none %}
        and period_start <= timestamptz '{{ read_until }}'
{% endif %}
),

gate as (
    select
        count(*) as half_hours,
        count(*) >= {{ half_hours_needed }}
            and {{ forecast_look_ahead_reached(read_until) }} as is_reached
    from population
),

dates as (
    select
        utc_date,
        row_number() over (order by utc_date) as date_number
    from population
    group by utc_date
),

counted_polls as (
    select
        revisions.period_start,
        revisions.horizon_seconds,
        revisions.forecast
    from {{ ref('int_carbon_forecast__revisions') }} as revisions
    inner join population
        on revisions.period_start = population.period_start
    where revisions.is_counted_poll
        and revisions.horizon_seconds >= 0
),

changes as (
    select
        period_start,
        horizon_seconds,
        forecast - lag(forecast) over (
            partition by period_start order by horizon_seconds desc
        ) as change
    from counted_polls
),

pairs as (
    select
        period_start,
        lag(change) over (
            partition by period_start order by horizon_seconds desc
        ) as earlier_change,
        change as later_change
    from changes
    where change is not null
),

change_sums as (
    select
        population.period_start,
        population.drift,
        sum(changes.change) as change_sum
    from population
    left join changes
        on population.period_start = changes.period_start
    group by population.period_start, population.drift
),

date_drift_sums as (
    select
        dates.date_number,
        count(*) as half_hours,
        sum(population.drift) as drift_sum
    from population
    inner join dates
        on population.utc_date = dates.utc_date
    group by dates.date_number
),

date_pair_sums as (
    select
        dates.date_number,
        count(*) as pairs,
        sum(pairs.earlier_change) as earlier_sum,
        sum(pairs.later_change) as later_sum,
        sum(pairs.earlier_change * pairs.earlier_change) as earlier_square_sum,
        sum(pairs.later_change * pairs.later_change) as later_square_sum,
        sum(pairs.earlier_change * pairs.later_change) as product_sum
    from pairs
    inner join population
        on pairs.period_start = population.period_start
    inner join dates
        on population.utc_date = dates.utc_date
    where pairs.earlier_change is not null
    group by dates.date_number
),

-- Draw 0 is the population itself; draws 1 to 1,000 resample it by date.
picks as (
    select
        0 as draw,
        date_number
    from dates

    union all

    select
        draws.draw,
        {{ bootstrap_date_number(bootstrap_seed, 'draws.draw', 'picked.pick', 'date_count.dates') }}
            as date_number
    from gate
    cross join (select count(*) as dates from dates) as date_count
    cross join generate_series(1, {{ bootstrap_draws }}) as draws (draw)
    cross join generate_series(1, date_count.dates) as picked (pick)
    where gate.is_reached
),

draw_means as (
    select
        picks.draw,
        sum(date_drift_sums.drift_sum)::numeric / sum(date_drift_sums.half_hours) as mean_drift
    from picks
    inner join date_drift_sums
        on picks.date_number = date_drift_sums.date_number
    group by picks.draw
),

draw_pair_sums as (
    select
        picks.draw,
        sum(date_pair_sums.pairs)::numeric as pairs,
        sum(date_pair_sums.earlier_sum) as earlier_sum,
        sum(date_pair_sums.later_sum) as later_sum,
        sum(date_pair_sums.earlier_square_sum) as earlier_square_sum,
        sum(date_pair_sums.later_square_sum) as later_square_sum,
        sum(date_pair_sums.product_sum) as product_sum
    from picks
    inner join date_pair_sums
        on picks.date_number = date_pair_sums.date_number
    group by picks.draw
),

-- Pearson's r from the sums; null when either change has no variance.
draw_correlations as (
    select
        draw,
        (pairs * product_sum - earlier_sum * later_sum)
            / nullif(
                sqrt(
                    (pairs * earlier_square_sum - earlier_sum * earlier_sum)
                    * (pairs * later_square_sum - later_sum * later_sum)
                ),
                0
            ) as correlation
    from draw_pair_sums
),

draw_values as (
    select 'mean drift' as measure, draw, mean_drift as value
    from draw_means

    union all

    select 'correlation' as measure, draw, correlation as value
    from draw_correlations
),

measures as (
    select 'mean drift' as measure
    union all
    select 'correlation' as measure
),

measure_values as (
    select
        measures.measure,
        min(draw_values.value) filter (where draw_values.draw = 0) as observed,
        count(draw_values.value) filter (where draw_values.draw > 0) as draws_with_value,
        percentile_cont(0.05) within group (order by draw_values.value)
            filter (where draw_values.draw > 0) as interval_low,
        percentile_cont(0.95) within group (order by draw_values.value)
            filter (where draw_values.draw > 0) as interval_high
    from measures
    left join draw_values
        on measures.measure = draw_values.measure
    group by measures.measure
),

-- Before the gate the observed values are worked out but never shown.
shown_values as (
    select
        measure_values.measure,
        case when gate.is_reached then round(measure_values.observed, 6) end as value,
        case when gate.is_reached
            and measure_values.draws_with_value >= {{ bootstrap_min_draws_with_value }}
            then round(measure_values.interval_low::numeric, 6)
        end as interval_low,
        case when gate.is_reached
            and measure_values.draws_with_value >= {{ bootstrap_min_draws_with_value }}
            then round(measure_values.interval_high::numeric, 6)
        end as interval_high,
        case when gate.is_reached then measure_values.draws_with_value end as draws_with_value,
        case when gate.is_reached
            and measure_values.draws_with_value < {{ bootstrap_min_draws_with_value }}
            then '; interval unavailable: fewer than {{ bootstrap_min_draws_with_value }} of {{ bootstrap_draws }} draws have a value'
            else ''
        end as interval_note
    from measure_values
    cross join gate
),

change_counts as (
    select
        (select count(*) from changes where change is not null) as changes,
        (select count(*) from changes where change = 0) as zero_changes,
        (select count(*) from pairs where earlier_change is not null) as pairs,
        (select count(*) from change_sums where change_sum is distinct from drift)
            as half_hours_not_summing
),

output_rows as (
    select
        1 as section,
        1 as position,
        'population: complete half-hours ({{ half_hours_needed }} needed)' as item,
        gate.half_hours as n,
        null::numeric as value,
        null::numeric as interval_low,
        null::numeric as interval_high,
        null::bigint as draws_with_value,
        null::text as detail
    from gate

    union all

    select 1, 2, 'UTC dates of the population', count(*), null, null, null, null, null
    from dates

    union all

    select 1, 3, 'read possible', null, null, null, null, null,
        case when gate.is_reached then 'yes' else 'no' end
    from gate

    union all

    select 2, 1, 'mean drift, gCO2/kWh', gate.half_hours,
        shown_values.value, shown_values.interval_low, shown_values.interval_high,
        shown_values.draws_with_value,
        'not called pre-registered: an exploration on 11 September 2026 computed a near preview'
            || shown_values.interval_note
    from shown_values
    cross join gate
    where shown_values.measure = 'mean drift'

    union all

    select 3, 1, 'Pearson''s r of consecutive changes within a half-hour', change_counts.pairs,
        shown_values.value, shown_values.interval_low, shown_values.interval_high,
        shown_values.draws_with_value,
        'not called pre-registered: a published analysis (gridcast) reports negative values on overlapping dates'
            || shown_values.interval_note
    from shown_values
    cross join change_counts
    where shown_values.measure = 'correlation'

    union all

    select 3, 2, 'pairs of consecutive changes within a half-hour', pairs,
        null, null, null, null, null
    from change_counts

    union all

    select 3, 3, 'changes between consecutive counted polls', changes,
        null, null, null, null, null
    from change_counts

    union all

    select 3, 4, 'share of changes equal to 0', change_counts.changes,
        case when gate.is_reached
            then round(change_counts.zero_changes::numeric / nullif(change_counts.changes, 0), 6)
        end,
        null, null, null, null
    from change_counts
    cross join gate

    union all

    select 3, 5, 'population half-hours whose changes do not sum to their drift (expected 0)',
        half_hours_not_summing, null, null, null, null, null
    from change_counts

    union all

    select 4, 1, 'reading', null, null, null, null, null,
        'Q1 shows the usual direction of change, not whether the forecast is efficient.'
)

select
    section,
    position,
    item,
    n,
    value,
    interval_low,
    interval_high,
    draws_with_value,
    detail
from output_rows
order by section, position
