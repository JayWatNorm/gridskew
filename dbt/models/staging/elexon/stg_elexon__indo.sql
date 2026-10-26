select
    settlement_date,
    settlement_period,
    start_time,
    publish_time,
    demand as demand_mw,
    retrieved_at
from {{ source('elexon', 'elexon_indo') }}
