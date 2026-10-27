select
    settlement_date,
    settlement_period,
    start_time,
    fuel_type,
    publish_time,
    generation as generation_mw,
    retrieved_at
from {{ source('elexon', 'elexon_fuelhh') }}
