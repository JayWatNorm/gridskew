{{ config(group='analysis') }}

-- Q29: how does the error of the API's own final carbon forecast vary by
-- month and by time of day, over a year of history? Reading rule (research
-- questions, registered 27 October 2026, before any figure was read):
--
-- Window: settlement dates final_forecast_error_from to
-- final_forecast_error_until, both included (22 August 2025 to 16 August
-- 2026), half-hours from the settlement calendar. Error: the outturn minus
-- the API's final forecast from the latest capture holding an observed
-- value, so a positive error means the half-hour was dirtier than forecast.
-- Population: every half-hour in the window with an error.
--
-- Each split stands on its own: the month of the settlement date (a month
-- the window only partly covers is labelled with its days), and Q1's four
-- blocks of the half-hour's UTC start. Every row gives the half-hours in the
-- window, with and without an error, and over those with one: the mean
-- absolute error, the mean error, the median error and the share of errors
-- equal to 0. Describe only: nothing is tested. The overall row was partly
-- seen before the rule (a September 2026 exploration: mean +0.43, median 0).

{% set error_from = var('final_forecast_error_from') %}
{% set error_until = var('final_forecast_error_until') %}

with window_days as (
    select date '{{ error_from }}' + day_offset as settlement_date
    from generate_series(0, date '{{ error_until }}' - date '{{ error_from }}') as offsets (day_offset)
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

final_forecast_errors as (
    select
        period_start,
        actual - api_final_forecast as error_api_final
    from {{ ref('int_carbon_outturn__latest') }}
    where api_final_forecast is not null
),

months as (
    select
        date_trunc('month', settlement_date)::date as month_start,
        min(settlement_date) as first_day,
        max(settlement_date) as last_day
    from window_days
    group by 1
),

month_labels as (
    select
        month_start,
        case
            when first_day > month_start
                or last_day < (month_start + interval '1 month' - interval '1 day')::date
                then to_char(month_start, 'YYYY-MM') || ' (partial: '
                    || extract(day from first_day) || ' to ' || extract(day from last_day) || ')'
            else to_char(month_start, 'YYYY-MM')
        end as month_label
    from months
),

window_errors as (
    select
        month_labels.month_label,
        case
            when extract(hour from window_half_hours.period_start at time zone 'UTC') < 6 then '00:00-05:59'
            when extract(hour from window_half_hours.period_start at time zone 'UTC') < 12 then '06:00-11:59'
            when extract(hour from window_half_hours.period_start at time zone 'UTC') < 18 then '12:00-17:59'
            else '18:00-23:59'
        end as utc_block,
        final_forecast_errors.error_api_final
    from window_half_hours
    inner join month_labels
        on date_trunc('month', window_half_hours.settlement_date)::date = month_labels.month_start
    left join final_forecast_errors
        on window_half_hours.period_start = final_forecast_errors.period_start
),

cells as (
    select
        1 as section,
        'all half-hours (partly seen before the rule: mean +0.43, median 0)' as item,
        error_api_final
    from window_errors

    union all

    select 2, 'settlement month ' || month_label, error_api_final
    from window_errors

    union all

    select 3, 'UTC block ' || utc_block, error_api_final
    from window_errors
)

select
    section,
    item,
    count(*) as half_hours,
    count(error_api_final) as half_hours_with_error,
    count(*) - count(error_api_final) as half_hours_without_error,
    round(avg(abs(error_api_final)), 3) as mean_absolute_error,
    round(avg(error_api_final), 3) as mean_error,
    round((percentile_cont(0.5) within group (order by error_api_final))::numeric, 3) as median_error,
    round(
        count(*) filter (where error_api_final = 0)::numeric / nullif(count(error_api_final), 0),
        3
    ) as zero_error_share
from cells
group by section, item
order by section, item
