{{ config(group='analysis', access='private') }}

-- One row per half-hour with a FUELHH wind outturn: its first publication,
-- the latest WINDFOR issue published strictly before the half-hour starts,
-- and that issue's hourly point for the hour containing the half-hour's
-- start (both halves of an hour share it). If the latest issue holds no
-- point for the hour, the forecast is null: an older issue is never used in
-- its place. The delivered wind error is outturn minus forecast; negative
-- means less wind than forecast. The cohort curtailment add-back is not
-- here: the Q30 analysis computes it from int_shortfall_by_unit_period, so
-- this view stays outside the period tables' lineage.

with first_outturn as (
    select distinct on (start_time)
        settlement_date,
        settlement_period,
        start_time,
        publish_time,
        generation_mw
    from {{ ref('stg_elexon__fuelhh') }}
    where fuel_type = 'WIND'
    order by start_time asc, publish_time asc
),

issues as (
    select distinct publish_time
    from {{ ref('stg_elexon__windfor') }}
),

latest_issue as (
    select
        first_outturn.start_time,
        max(issues.publish_time) as publish_time
    from first_outturn
    inner join issues
        on issues.publish_time < first_outturn.start_time
    group by first_outturn.start_time
)

select
    first_outturn.settlement_date,
    first_outturn.settlement_period,
    first_outturn.start_time,
    first_outturn.publish_time as outturn_published_at,
    first_outturn.generation_mw as outturn_mw,
    latest_issue.publish_time as forecast_published_at,
    forecast_point.generation_mw as forecast_mw,
    first_outturn.generation_mw - forecast_point.generation_mw as delivered_wind_error_mw
from first_outturn
left join latest_issue
    on first_outturn.start_time = latest_issue.start_time
left join {{ ref('stg_elexon__windfor') }} as forecast_point
    on latest_issue.publish_time = forecast_point.publish_time
    and date_trunc('hour', first_outturn.start_time, 'UTC') = forecast_point.start_time
