{{ config(group='analysis') }}

-- Q10: how accurate is the carbon-intensity forecast at each lead time?
-- Reading rule (research questions, recorded 12 October 2026): read once,
-- after Q1's one look, at the same analysis_read_until. Mean absolute error
-- and bias of the forecast at 24 hours, 4 hours and final, against outturn,
-- on one common sample: Q1's one-look half-hours that have all three
-- horizons, with each horizon's n and the half-hours dropped reported. A
-- secondary split by settlement-date type (weekday, weekend, bank holiday in
-- England and Wales) is described with n; nothing is tested.
--
-- Error is actual minus forecast, so a positive bias means half-hours were
-- dirtier than forecast. A half-hour's settlement date is its local
-- (Europe/London) date. Until Q1's population is reached, with at least
-- three slot polls stored after the cutoff (Q1's gate), it returns counts and
-- no accuracy figure. Every listed cell appears, with zero half-hours if
-- it has none.

{% set half_hours_needed = var('forecast_drift_half_hours_needed') %}
{% set read_until = analysis_read_until() %}

with q1_half_hours as (
    select
        period_start,
        error_24h,
        error_4h,
        error_final
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

q1_horizon_errors as (
    select
        q1_half_hours.period_start,
        horizons.horizon,
        horizons.error
    from q1_half_hours
    cross join lateral (
        values
            ('24 hours', q1_half_hours.error_24h),
            ('4 hours', q1_half_hours.error_4h),
            ('final', q1_half_hours.error_final)
    ) as horizons (horizon, error)
),

common_sample as (
    select
        period_start,
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
        common_sample.settlement_date_type,
        q1_horizon_errors.horizon,
        q1_horizon_errors.error
    from common_sample
    inner join q1_horizon_errors
        on common_sample.period_start = q1_horizon_errors.period_start
),

listed_horizons as (
    select horizon
    from (values ('24 hours'), ('4 hours'), ('final')) as listed (horizon)
),

listed_date_types as (
    select settlement_date_type
    from (values ('weekday'), ('weekend'), ('bank holiday')) as listed (settlement_date_type)
),

counts as (
    select
        1 as section,
        'Q1 one-look half-hours ({{ half_hours_needed }} needed)' as item,
        null::text as horizon,
        count(*) as half_hours,
        min(period_start) as first_half_hour,
        max(period_start) as last_half_hour
    from q1_half_hours

    union all

    select
        1,
        'common sample: all three horizons',
        null,
        count(*),
        min(period_start),
        max(period_start)
    from common_sample

    union all

    select
        1,
        'with a forecast at this horizon',
        listed_horizons.horizon,
        count(q1_horizon_errors.error),
        min(q1_horizon_errors.period_start) filter (where q1_horizon_errors.error is not null),
        max(q1_horizon_errors.period_start) filter (where q1_horizon_errors.error is not null)
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
        max(q1_horizon_errors.period_start)
    from listed_horizons
    left join q1_horizon_errors
        on listed_horizons.horizon = q1_horizon_errors.horizon
        and q1_horizon_errors.error is null
    group by listed_horizons.horizon
),

accuracy as (
    select
        2 as section,
        'accuracy on the common sample' as item,
        listed_horizons.horizon,
        count(common_sample_errors.error) as half_hours,
        avg(abs(common_sample_errors.error)) as mean_absolute_error,
        avg(common_sample_errors.error) as bias,
        min(common_sample_errors.period_start) as first_half_hour,
        max(common_sample_errors.period_start) as last_half_hour
    from listed_horizons
    left join common_sample_errors
        on listed_horizons.horizon = common_sample_errors.horizon
    group by listed_horizons.horizon

    union all

    select
        3,
        'settlement date: ' || listed_date_types.settlement_date_type,
        listed_horizons.horizon,
        count(common_sample_errors.error),
        avg(abs(common_sample_errors.error)),
        avg(common_sample_errors.error),
        min(common_sample_errors.period_start),
        max(common_sample_errors.period_start)
    from listed_date_types
    cross join listed_horizons
    left join common_sample_errors
        on listed_date_types.settlement_date_type = common_sample_errors.settlement_date_type
        and listed_horizons.horizon = common_sample_errors.horizon
    group by listed_date_types.settlement_date_type, listed_horizons.horizon
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
    population.is_reached as population_is_reached
from counts
cross join population

union all

select
    accuracy.section,
    accuracy.item,
    accuracy.horizon,
    accuracy.half_hours,
    case when population.is_reached then round(accuracy.mean_absolute_error, 3) end,
    case when population.is_reached then round(accuracy.bias, 3) end,
    accuracy.first_half_hour,
    accuracy.last_half_hour,
    population.is_reached
from accuracy
cross join population

order by section, item, horizon
