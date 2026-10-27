select
    start_time,
    publish_time,
    generation as generation_mw,
    retrieved_at
from {{ source('elexon', 'elexon_windfor') }}
