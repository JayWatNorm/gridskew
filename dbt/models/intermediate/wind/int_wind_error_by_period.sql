{{ config(group='analysis', access='private') }}

-- One row per half-hour with a FUELHH wind outturn: its first publication,
-- the latest WINDFOR issue published strictly before the half-hour starts,
-- that issue's hourly point for the hour containing the half-hour's start
-- (both halves of an hour share it), and the cohort wind curtailment
-- add-back. If the latest issue holds no point for the hour, the forecast is
-- null: an older issue is never used in its place.
--
-- The add-back is the signed sum of instructed_deviation_mwh (PN minus
-- expected) over cohort wind units with an acceptance in the half-hour,
-- times 2 for MW. It is 0 when no wind unit had an acceptance, and unknown
-- (null) when any wind unit with an acceptance has a null instructed
-- deviation. Wind error is outturn plus add-back minus forecast; the
-- delivered wind error is outturn minus forecast. Negative means less wind
-- than forecast.

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
),

wind_unit_periods as (
    select
        shortfall.settlement_date,
        shortfall.settlement_period,
        shortfall.acceptance_count,
        shortfall.instructed_deviation_mwh
    from {{ ref('int_shortfall_by_unit_period') }} as shortfall
    inner join {{ ref('int_elexon__bm_unit_cohort') }} as cohort
        on shortfall.national_grid_bm_unit = cohort.national_grid_bm_unit
    -- Only dates with an outturn, so the table's unit and date index serves
    -- the join.
    inner join (select distinct settlement_date from first_outturn) as outturn_dates
        on shortfall.settlement_date = outturn_dates.settlement_date
    where cohort.fuel_group = 'wind'
),

wind_curtailment as (
    select
        settlement_date,
        settlement_period,
        count(*) filter (where acceptance_count > 0) as wind_units_with_acceptance,
        count(*) filter (
            where acceptance_count > 0 and instructed_deviation_mwh is null
        ) as wind_units_with_acceptance_and_null_deviation,
        count(*) filter (where instructed_deviation_mwh is null) as wind_units_with_null_deviation,
        sum(instructed_deviation_mwh) filter (where acceptance_count > 0) as instructed_deviation_mwh
    from wind_unit_periods
    group by settlement_date, settlement_period
),

paired as (
    select
        first_outturn.settlement_date,
        first_outturn.settlement_period,
        first_outturn.start_time,
        first_outturn.publish_time as outturn_published_at,
        first_outturn.generation_mw as outturn_mw,
        latest_issue.publish_time as forecast_published_at,
        forecast_point.generation_mw as forecast_mw,
        coalesce(wind_curtailment.wind_units_with_acceptance, 0) as wind_units_with_acceptance,
        coalesce(wind_curtailment.wind_units_with_acceptance_and_null_deviation, 0)
            as wind_units_with_acceptance_and_null_deviation,
        coalesce(wind_curtailment.wind_units_with_null_deviation, 0) as wind_units_with_null_deviation,
        case
            when coalesce(wind_curtailment.wind_units_with_acceptance, 0) = 0 then 0
            when wind_curtailment.wind_units_with_acceptance_and_null_deviation > 0 then null
            else 2 * wind_curtailment.instructed_deviation_mwh
        end as curtailment_add_back_mw
    from first_outturn
    left join latest_issue
        on first_outturn.start_time = latest_issue.start_time
    left join {{ ref('stg_elexon__windfor') }} as forecast_point
        on latest_issue.publish_time = forecast_point.publish_time
        and date_trunc('hour', first_outturn.start_time, 'UTC') = forecast_point.start_time
    left join wind_curtailment
        on first_outturn.settlement_date = wind_curtailment.settlement_date
        and first_outturn.settlement_period = wind_curtailment.settlement_period
)

select
    settlement_date,
    settlement_period,
    start_time,
    outturn_published_at,
    outturn_mw,
    forecast_published_at,
    forecast_mw,
    wind_units_with_acceptance,
    wind_units_with_acceptance_and_null_deviation,
    wind_units_with_null_deviation,
    round(curtailment_add_back_mw, 6) as curtailment_add_back_mw,
    round(outturn_mw + curtailment_add_back_mw - forecast_mw, 6) as wind_error_mw,
    outturn_mw - forecast_mw as delivered_wind_error_mw
from paired
