select
    national_grid_bm_unit,
    bm_unit,
    settlement_date,
    settlement_period,
    bm_unit_applicable_balancing_services_volume as qas_mwh,
    retrieved_at
from {{ source('elexon', 'elexon_qas') }}
