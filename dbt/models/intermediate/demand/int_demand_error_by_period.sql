{{ config(group='analysis', access='private') }}

-- One row per half-hour with a demand outturn (INDO): its first issue, the
-- last national demand forecast (NDF, boundary N) issued before the
-- half-hour starts, and the error, outturn minus forecast. Positive means
-- the country used more than forecast. A half-hour with no forecast issued
-- before it keeps its row, with null forecast and error.

with first_outturn as (
    select distinct on (start_time)
        settlement_date,
        settlement_period,
        start_time,
        publish_time,
        demand_mw
    from {{ ref('stg_elexon__indo') }}
    order by start_time asc, publish_time asc
),

last_forecast as (
    select distinct on (start_time)
        start_time,
        publish_time,
        demand_mw
    from {{ ref('stg_elexon__ndf') }}
    where boundary = 'N'
        and publish_time < start_time
    order by start_time asc, publish_time desc
)

select
    first_outturn.settlement_date,
    first_outturn.settlement_period,
    first_outturn.start_time,
    first_outturn.publish_time as outturn_published_at,
    first_outturn.demand_mw as outturn_demand_mw,
    last_forecast.publish_time as forecast_published_at,
    last_forecast.demand_mw as forecast_demand_mw,
    first_outturn.demand_mw - last_forecast.demand_mw as demand_error_final_mw
from first_outturn
left join last_forecast
    on first_outturn.start_time = last_forecast.start_time
