select
    period_start,
    period_end,
    retrieved_at,
    forecast,
    actual,
    intensity_index
from {{ source('intensity', 'carbon_intensity_forecast') }}