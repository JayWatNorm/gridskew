{{ config(group='analysis') }}

-- Q10: how accurate is the carbon-intensity forecast at each lead time?
-- Reading rule (research questions, recorded 12 October 2026): read once,
-- after Q1's one look, at the same analysis_read_until. Mean absolute error
-- and bias of the forecast about 24 hours, about 4 hours and about half an
-- hour ahead, against outturn, on one common sample: Q1's one-look
-- half-hours that have all three, with each lead time's n and the
-- half-hours dropped reported. A secondary split by settlement-date type
-- (weekday, weekend, bank holiday in England and Wales) is described with n;
-- nothing is tested.
--
-- Amended 31 October 2026, before any read: the lead times are named as they
-- are, and an interval, two simple reference forecasts, the API's own final
-- forecast and the same figures on the half-hours an exploration of
-- 11 September 2026 did not see are added beside the result.
-- - Lead times. The 24-hour and 4-hour forecasts are the counted poll
--   nearest that horizon, null when none is within half an hour of it; the
--   half-hour forecast is Q1's final. Until 10 September 2026 10:30 UTC the
--   archive polled on the half-hour, so the three were made about a second
--   short of 24 hours, 4 hours and 30 minutes ahead; since then it polls at
--   :05 and :35, so about 23 hours 55 minutes, 3 hours 55 minutes and 25
--   minutes, or 30 minutes earlier when that poll is missing or not counted
--   (a fall-back: a 24-hour or 4-hour pick made more than 86,400 or 14,400
--   seconds ahead; the half-hour pick has none). Each lead time's shortest,
--   median and longest horizon in seconds, its fall-back picks, and its
--   picks on each capture schedule (forecast_poll_schedules) are reported.
-- - Interval. A 90% day-block bootstrap interval for every MAE and bias: the
--   common sample's settlement dates, numbered 1 to n in ascending order,
--   resampled with replacement in 1,000 draws of n picks each, picked from md5
--   of the seed q10-2026-10-31, the draw and the pick (bootstrap_date_number);
--   the interval is the 5th and 95th percentile_cont of the draws. One set of
--   draws serves the whole output; a draw holding none of a figure's
--   half-hours has no value for it, and with fewer than 900 of the 1,000 draws
--   holding a value its interval is unavailable. Descriptive.
-- - Context, not a test, on the same half-hours, each with n, MAE, bias and
--   interval; error is actual minus the reference: persistence (the outturn of
--   the latest half-hour that had ended at or before each pick's poll) and the
--   same half-hour the day before (the outturn 24 hours earlier in UTC). Both
--   use outturn as later stored. At the 24-hour lead time the day-before
--   half-hour ends 25 to 30 minutes after the forecast is made, 55 to 60 for a
--   fall-back pick. A half-hour whose reference has no outturn is left out of
--   that reference and counted.
-- - Consistency check. The API's own final forecast (error_api_final) on the
--   same half-hours. Its timing is not documented, and its error on part of
--   this period was seen before 31 October 2026.
-- - Part not seen. The same figures on the half-hours after
--   forecast_explored_until (10 September 2026 05:30 UTC), which the
--   exploration did not see, beside the result; they never change it.
-- Not added: a time-of-day split, and an MAE by horizon from the archive.
--
-- Error is actual minus forecast, so a positive bias means half-hours were
-- dirtier than forecast. A half-hour's settlement date is its local
-- (Europe/London) date. Until Q1's population is reached, with at least
-- three slot polls stored after the cutoff (Q1's gate), it returns counts of
-- half-hours and no accuracy figure, interval or horizon. Read at least 8
-- days after the cutoff, so outturn has settled. Every listed cell appears,
-- with zero half-hours if it has none.

{% set half_hours_needed = var('forecast_drift_half_hours_needed') %}
{% set read_until = analysis_read_until() %}
{% set bootstrap_seed = 'q10-2026-10-31' %}
{% set bootstrap_draws = 1000 %}
{% set bootstrap_min_draws_with_value = 900 %}
{% set persistence_item = "reference, persistence: outturn of the latest half-hour ended at or before the pick''s poll" %}
{% set persistence_left_out_item = 'reference, persistence: left out, no outturn for the reference half-hour' %}
{% set day_before_item = 'reference, the same half-hour the day before: outturn 24 hours earlier' %}
{% set day_before_left_out_item = 'reference, the same half-hour the day before: left out, no outturn for the reference half-hour' %}
{% set api_final_item = 'consistency check: error_api_final on the common sample' %}
{% set api_final_left_out_item = 'consistency check: left out, no error_api_final' %}
{% set part_not_seen_item = 'the part not seen' %}
{% set last_listed_year = (var('bank_holidays_england_and_wales') | last)[:4] | int %}
{% if read_until is not none %}
    {% set last_read_year = (read_until | string)[:4] | int %}
{% else %}
    {% set last_read_year = modules.datetime.date.today().year %}
{% endif %}
{% if last_read_year > last_listed_year %}
    {{ exceptions.raise_compiler_error(
        "Q10 lists bank holidays only to " ~ last_listed_year
        ~ "; add the later years to bank_holidays_england_and_wales before reading to "
        ~ last_read_year
    ) }}
{% endif %}

with q1_half_hours as (
    select
        period_start,
        actual,
        error_24h,
        error_4h,
        error_final,
        error_api_final,
        horizon_24h_seconds,
        horizon_4h_seconds,
        final_horizon_seconds
    from {{ ref('int_carbon_error_by_period') }}
    where has_complete_trajectory
        and period_start >= timestamptz '{{ var("forecast_drift_population_from") }}'
{% if read_until is not none %}
        and period_start <= timestamptz '{{ read_until }}'
{% endif %}
),

population as (
    select
        count(*) >= {{ half_hours_needed }}
            and {{ forecast_look_ahead_reached(read_until) }} as is_reached
    from q1_half_hours
),

-- The half-hour pick has no target: an earlier final is more than 30 minutes
-- ahead, so the trajectory is incomplete, and it never falls back.
q1_horizon_errors as (
    select
        q1_half_hours.period_start,
        horizons.horizon,
        horizons.error,
        horizons.horizon_seconds,
        horizons.target_seconds
    from q1_half_hours
    cross join lateral (
        values
            ('about 24 hours', q1_half_hours.error_24h, q1_half_hours.horizon_24h_seconds, 86400),
            ('about 4 hours', q1_half_hours.error_4h, q1_half_hours.horizon_4h_seconds, 14400),
            ('about half an hour', q1_half_hours.error_final, q1_half_hours.final_horizon_seconds, null)
    ) as horizons (horizon, error, horizon_seconds, target_seconds)
),

common_sample as (
    select
        period_start,
        actual,
        error_api_final,
        (period_start at time zone 'Europe/London')::date as settlement_date,
        case
            when (period_start at time zone 'Europe/London')::date in (
{%- for holiday in var('bank_holidays_england_and_wales') %}
                date '{{ holiday }}'{{ ',' if not loop.last }}
{%- endfor %}
            ) then 'bank holiday'
            when extract(isodow from period_start at time zone 'Europe/London') in (6, 7)
                then 'weekend'
            else 'weekday'
        end as settlement_date_type
    from q1_half_hours
    where error_24h is not null
        and error_4h is not null
        and error_final is not null
),

common_sample_errors as (
    select
        common_sample.period_start,
        common_sample.settlement_date,
        common_sample.settlement_date_type,
        common_sample.actual,
        q1_horizon_errors.horizon,
        q1_horizon_errors.error,
        q1_horizon_errors.horizon_seconds,
        q1_horizon_errors.horizon_seconds > q1_horizon_errors.target_seconds as is_fall_back,
        common_sample.period_start
            - q1_horizon_errors.horizon_seconds * interval '1 second' as poll_time
    from common_sample
    inner join q1_horizon_errors
        on common_sample.period_start = q1_horizon_errors.period_start
),

outturn as (
    select
        period_start,
        actual
    from {{ ref('int_carbon_outturn__latest') }}
),

-- The latest half-hour that had ended at or before the pick's poll: a poll
-- made exactly as a half-hour ends uses that half-hour.
persistence as (
    select
        picks.period_start,
        picks.settlement_date,
        picks.horizon,
        picks.actual - outturn.actual as error
    from common_sample_errors as picks
    left join outturn
        on outturn.period_start = date_bin(
            interval '30 minutes', picks.poll_time, timestamptz '2000-01-01 00:00:00+00'
        ) - interval '30 minutes'
),

-- 24 hours earlier in UTC, so across a clock change it is not the same
-- clock time.
day_before as (
    select
        common_sample.period_start,
        common_sample.settlement_date,
        common_sample.actual - outturn.actual as error
    from common_sample
    left join outturn
        on outturn.period_start = common_sample.period_start - interval '24 hours'
),

listed_horizons as (
    select horizon
    from (
        values ('about 24 hours'), ('about 4 hours'), ('about half an hour')
    ) as listed (horizon)
),

listed_date_types as (
    select settlement_date_type
    from (values ('weekday'), ('weekend'), ('bank holiday')) as listed (settlement_date_type)
),

listed_schedules as (
    select schedule
    from (
        values
{%- for schedule in forecast_poll_schedules() %}
            ('{{ schedule.label | replace("'", "''") }}'){{ ',' if not loop.last }}
{%- endfor %}
    ) as listed (schedule)
),

-- Every half-hour of every cell with an accuracy figure and an interval.
cell_errors as (
    select
        2 as section,
        'accuracy on the common sample' as item,
        horizon,
        period_start,
        settlement_date,
        error
    from common_sample_errors

    union all

    select
        3,
        'settlement date: ' || settlement_date_type,
        horizon,
        period_start,
        settlement_date,
        error
    from common_sample_errors

    union all

    select 4, '{{ persistence_item }}', horizon, period_start, settlement_date, error
    from persistence
    where error is not null

    union all

    select 4, '{{ day_before_item }}', null, period_start, settlement_date, error
    from day_before
    where error is not null

    union all

    select 5, '{{ api_final_item }}', null, period_start, settlement_date, error_api_final
    from common_sample
    where error_api_final is not null

    union all

    select 6, '{{ part_not_seen_item }}', horizon, period_start, settlement_date, error
    from common_sample_errors
    where period_start > timestamptz '{{ var("forecast_explored_until") }}'
),

listed_cells as (
    select
        2 as section,
        'accuracy on the common sample' as item,
        horizon
    from listed_horizons

    union all

    select 3, 'settlement date: ' || listed_date_types.settlement_date_type, listed_horizons.horizon
    from listed_date_types
    cross join listed_horizons

    union all

    select 4, '{{ persistence_item }}', horizon
    from listed_horizons

    union all

    select 4, '{{ day_before_item }}', null

    union all

    select 5, '{{ api_final_item }}', null

    union all

    select 6, '{{ part_not_seen_item }}', horizon
    from listed_horizons
),

cell_accuracy as (
    select
        listed_cells.section,
        listed_cells.item,
        listed_cells.horizon,
        count(cell_errors.error) as half_hours,
        avg(abs(cell_errors.error)) as mean_absolute_error,
        avg(cell_errors.error) as bias,
        min(cell_errors.period_start) as first_half_hour,
        max(cell_errors.period_start) as last_half_hour
    from listed_cells
    left join cell_errors
        on listed_cells.section = cell_errors.section
        and listed_cells.item = cell_errors.item
        and listed_cells.horizon is not distinct from cell_errors.horizon
    group by listed_cells.section, listed_cells.item, listed_cells.horizon
),

dates as (
    select
        settlement_date,
        row_number() over (order by settlement_date) as date_number
    from common_sample
    group by settlement_date
),

cell_date_sums as (
    select
        cell_errors.section,
        cell_errors.item,
        cell_errors.horizon,
        dates.date_number,
        count(*) as half_hours,
        sum(abs(cell_errors.error)) as absolute_error_sum,
        sum(cell_errors.error) as error_sum
    from cell_errors
    inner join dates
        on cell_errors.settlement_date = dates.settlement_date
    group by cell_errors.section, cell_errors.item, cell_errors.horizon, dates.date_number
),

picks as (
    select
        draws.draw,
        {{ bootstrap_date_number(bootstrap_seed, 'draws.draw', 'picked.pick', 'date_count.dates') }}
            as date_number
    from population
    cross join (select count(*) as dates from dates) as date_count
    cross join generate_series(1, {{ bootstrap_draws }}) as draws (draw)
    cross join generate_series(1, date_count.dates) as picked (pick)
    where population.is_reached
),

-- A draw holding none of a cell's half-hours has no row for it.
draw_accuracy as (
    select
        cell_date_sums.section,
        cell_date_sums.item,
        cell_date_sums.horizon,
        picks.draw,
        sum(cell_date_sums.absolute_error_sum)::numeric
            / sum(cell_date_sums.half_hours) as mean_absolute_error,
        sum(cell_date_sums.error_sum)::numeric / sum(cell_date_sums.half_hours) as bias
    from picks
    inner join cell_date_sums
        on picks.date_number = cell_date_sums.date_number
    group by cell_date_sums.section, cell_date_sums.item, cell_date_sums.horizon, picks.draw
),

cell_intervals as (
    select
        section,
        item,
        horizon,
        count(*) as draws_with_value,
        percentile_cont(0.05) within group (order by mean_absolute_error) as mae_interval_low,
        percentile_cont(0.95) within group (order by mean_absolute_error) as mae_interval_high,
        percentile_cont(0.05) within group (order by bias) as bias_interval_low,
        percentile_cont(0.95) within group (order by bias) as bias_interval_high
    from draw_accuracy
    group by section, item, horizon
),

pick_horizons as (
    select
        listed_horizons.horizon,
        min(common_sample_errors.horizon_seconds) as shortest_horizon_seconds,
        percentile_cont(0.5) within group (order by common_sample_errors.horizon_seconds)
            as median_horizon_seconds,
        max(common_sample_errors.horizon_seconds) as longest_horizon_seconds,
        count(*) filter (where common_sample_errors.is_fall_back) as fall_back_picks
    from listed_horizons
    left join common_sample_errors
        on listed_horizons.horizon = common_sample_errors.horizon
    group by listed_horizons.horizon
),

pick_schedules as (
    select
        horizon,
        {{ forecast_poll_schedule('poll_time') }} as schedule
    from common_sample_errors
),

counts as (
    select
        1 as section,
        'Q1 one-look half-hours ({{ half_hours_needed }} needed)' as item,
        null::text as horizon,
        count(*) as half_hours,
        min(period_start) as first_half_hour,
        max(period_start) as last_half_hour,
        null::text as detail
    from q1_half_hours

    union all

    select
        1,
        'common sample: all three horizons',
        null,
        count(*),
        min(period_start),
        max(period_start),
        null
    from common_sample

    union all

    select
        1,
        'with a forecast at this horizon',
        listed_horizons.horizon,
        count(q1_horizon_errors.error),
        min(q1_horizon_errors.period_start) filter (where q1_horizon_errors.error is not null),
        max(q1_horizon_errors.period_start) filter (where q1_horizon_errors.error is not null),
        null
    from listed_horizons
    left join q1_horizon_errors
        on listed_horizons.horizon = q1_horizon_errors.horizon
    group by listed_horizons.horizon

    union all

    select
        1,
        'dropped: no forecast at this horizon',
        listed_horizons.horizon,
        count(q1_horizon_errors.period_start),
        min(q1_horizon_errors.period_start),
        max(q1_horizon_errors.period_start),
        null
    from listed_horizons
    left join q1_horizon_errors
        on listed_horizons.horizon = q1_horizon_errors.horizon
        and q1_horizon_errors.error is null
    group by listed_horizons.horizon

    union all

    select
        1,
        'fall-back picks: made more than the lead time ahead',
        horizon,
        case when horizon <> 'about half an hour' then fall_back_picks end,
        null,
        null,
        case when horizon = 'about half an hour' then 'not applicable' end
    from pick_horizons

    union all

    select
        1,
        'picks made on the capture schedule: ' || listed_schedules.schedule,
        listed_horizons.horizon,
        count(pick_schedules.schedule),
        null,
        null,
        null
    from listed_schedules
    cross join listed_horizons
    left join pick_schedules
        on listed_schedules.schedule = pick_schedules.schedule
        and listed_horizons.horizon = pick_schedules.horizon
    group by listed_schedules.schedule, listed_horizons.horizon

    union all

    select
        4,
        '{{ persistence_left_out_item }}',
        listed_horizons.horizon,
        count(persistence.period_start),
        null,
        null,
        null
    from listed_horizons
    left join persistence
        on listed_horizons.horizon = persistence.horizon
        and persistence.error is null
    group by listed_horizons.horizon

    union all

    select 4, '{{ day_before_left_out_item }}', null, count(*), null, null, null
    from day_before
    where error is null

    union all

    select 5, '{{ api_final_left_out_item }}', null, count(*), null, null, null
    from common_sample
    where error_api_final is null
),

cell_labels as (
    select
        cell_accuracy.section,
        cell_accuracy.item,
        cell_accuracy.horizon,
        case
            when cell_accuracy.item = '{{ day_before_item }}'
                then 'context, not a test; outturn as later stored; at the 24-hour lead time '
                    || 'this half-hour ends 25 to 30 minutes after the forecast is made, '
                    || '55 to 60 for a fall-back pick'
            when cell_accuracy.section = 4
                then 'context, not a test; outturn as later stored'
            when cell_accuracy.section = 5
                then 'the API''s stored final forecast: its timing is not documented, and '
                    || 'its error on part of this period was seen before 31 October 2026'
            when cell_accuracy.section = 6
                then 'the part not seen: half-hours after '
                    || to_char(
                        timestamptz '{{ var("forecast_explored_until") }}' at time zone 'UTC',
                        'FMDD FMMonth YYYY HH24:MI'
                    )
                    || ' UTC; it never changes the result'
        end as label
    from cell_accuracy
),

cells as (
    select
        cell_accuracy.section,
        cell_accuracy.item,
        cell_accuracy.horizon,
        cell_accuracy.half_hours,
        cell_accuracy.mean_absolute_error,
        cell_accuracy.bias,
        cell_accuracy.first_half_hour,
        cell_accuracy.last_half_hour,
        coalesce(cell_intervals.draws_with_value, 0) as draws_with_value,
        coalesce(cell_intervals.draws_with_value, 0) >= {{ bootstrap_min_draws_with_value }}
            as has_interval,
        cell_intervals.mae_interval_low,
        cell_intervals.mae_interval_high,
        cell_intervals.bias_interval_low,
        cell_intervals.bias_interval_high,
        cell_labels.label,
        case when cell_accuracy.section = 2 then pick_horizons.shortest_horizon_seconds end
            as shortest_horizon_seconds,
        case when cell_accuracy.section = 2 then pick_horizons.median_horizon_seconds end
            as median_horizon_seconds,
        case when cell_accuracy.section = 2 then pick_horizons.longest_horizon_seconds end
            as longest_horizon_seconds
    from cell_accuracy
    inner join cell_labels
        on cell_accuracy.section = cell_labels.section
        and cell_accuracy.item = cell_labels.item
        and cell_accuracy.horizon is not distinct from cell_labels.horizon
    left join cell_intervals
        on cell_accuracy.section = cell_intervals.section
        and cell_accuracy.item = cell_intervals.item
        and cell_accuracy.horizon is not distinct from cell_intervals.horizon
    left join pick_horizons
        on cell_accuracy.horizon = pick_horizons.horizon
)

select
    counts.section,
    counts.item,
    counts.horizon,
    counts.half_hours,
    null::numeric as mean_absolute_error,
    null::numeric as bias,
    counts.first_half_hour,
    counts.last_half_hour,
    population.is_reached as population_is_reached,
    null::numeric as mae_interval_low,
    null::numeric as mae_interval_high,
    null::numeric as bias_interval_low,
    null::numeric as bias_interval_high,
    null::bigint as draws_with_value,
    null::numeric as shortest_horizon_seconds,
    null::numeric as median_horizon_seconds,
    null::numeric as longest_horizon_seconds,
    counts.detail
from counts
cross join population

union all

select
    cells.section,
    cells.item,
    cells.horizon,
    cells.half_hours,
    case when population.is_reached then round(cells.mean_absolute_error, 3) end,
    case when population.is_reached then round(cells.bias, 3) end,
    cells.first_half_hour,
    cells.last_half_hour,
    population.is_reached,
    case when population.is_reached and cells.has_interval
        then round(cells.mae_interval_low::numeric, 3)
    end,
    case when population.is_reached and cells.has_interval
        then round(cells.mae_interval_high::numeric, 3)
    end,
    case when population.is_reached and cells.has_interval
        then round(cells.bias_interval_low::numeric, 3)
    end,
    case when population.is_reached and cells.has_interval
        then round(cells.bias_interval_high::numeric, 3)
    end,
    case when population.is_reached then cells.draws_with_value end,
    case when population.is_reached then round(cells.shortest_horizon_seconds, 3) end,
    case when population.is_reached then round(cells.median_horizon_seconds::numeric, 3) end,
    case when population.is_reached then round(cells.longest_horizon_seconds, 3) end,
    nullif(
        concat_ws(
            '; ',
            cells.label,
            case when population.is_reached and not cells.has_interval
                then 'interval unavailable: fewer than {{ bootstrap_min_draws_with_value }} of {{ bootstrap_draws }} draws have a value'
            end
        ),
        ''
    )
from cells
cross join population

order by section, item, horizon
