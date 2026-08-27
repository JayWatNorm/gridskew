select
    bm_unit,
    national_grid_bm_unit,
    settlement_date,
    settlement_period,
    time_from,
    time_to,
    level_from,
    level_to,
    retrieved_at
from {{ source('elexon', 'elexon_pn') }}