select
    bm_unit,
    national_grid_bm_unit_id,
    psr_type,
    settlement_date,
    settlement_period,
    {{ settlement_period_start_utc(
        'settlement_date',
        'settlement_period'
    ) }} as period_start_utc,
    half_hour_end_time,
    settlement_run_type,
    quantity,
    retrieved_at
from {{ source('elexon', 'elexon_b1610') }}