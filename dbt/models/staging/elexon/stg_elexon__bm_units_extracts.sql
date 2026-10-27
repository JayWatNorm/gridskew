select
    extract_id,
    retrieved_at,
    row_count,
    unit_count
from {{ source('elexon', 'elexon_bm_units_extracts') }}
