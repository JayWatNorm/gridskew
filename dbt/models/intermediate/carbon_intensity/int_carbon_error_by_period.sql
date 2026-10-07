{{ config(group='analysis', access='private') }}

-- One row per half-hour that has both an archived forecast and an outturn.
-- Error is actual minus forecast: positive means the half-hour was dirtier
-- than forecast.

select
    forecasts.period_start,
    outturn.actual,
    forecasts.first_forecast,
    forecasts.forecast_24h,
    forecasts.forecast_4h,
    forecasts.final_forecast,
    outturn.api_final_forecast,
    outturn.actual - forecasts.forecast_24h as error_24h,
    outturn.actual - forecasts.forecast_4h as error_4h,
    outturn.actual - forecasts.final_forecast as error_final,
    outturn.actual - outturn.api_final_forecast as error_api_final,
    forecasts.drift,
    forecasts.has_complete_trajectory,
    forecasts.has_complete_trajectory_before_no_change_exclusion,
    forecasts.as_published_drift,
    forecasts.as_published_has_complete_trajectory
from {{ ref('int_carbon_forecast__by_period') }} as forecasts
inner join {{ ref('int_carbon_outturn__latest') }} as outturn
    on forecasts.period_start = outturn.period_start
