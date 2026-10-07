{{ config(group='analysis', access='private') }}

-- One row per half-hour: the latest capture that holds an observed value.
-- The outturn is re-captured for seven days and a value can be revised.

select distinct on (period_start)
    period_start,
    actual,
    forecast_final as api_final_forecast,
    retrieved_at as outturn_captured_at
from {{ ref('stg_carbon_intensity__outturn') }}
where actual is not null
order by period_start asc, retrieved_at desc
