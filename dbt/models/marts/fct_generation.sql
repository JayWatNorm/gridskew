{{ config(materialized='view', group='elexon_facts') }}

-- One row per B1610 BM unit and settlement period: the persisted period values
-- plus current registry mapping and evidence, published from
-- int_elexon__b1610_period_mapped, where the mapping is made.
--
-- Written without WITH: PostgreSQL flattens a view like this one into the
-- joins of the query that reads it (within join_collapse_limit), so a join
-- on the period key reaches the period table's unique index. A view with a
-- WITH list is not flattened, and a join reads all of it.

select
    bm_unit,
    settlement_date,
    settlement_period,
    period_start_utc,
    first_quantity_mwh,
    first_run_code,
    first_retrieved_at,
    first_source_national_grid_bm_unit_id,
    latest_quantity_mwh,
    latest_run_code,
    latest_retrieved_at,
    latest_source_national_grid_bm_unit_id,
    last_captured_at,
    mapping_status,
    mapped_national_grid_bm_unit,
    current_fuel_type,
    current_bm_unit_type,
    current_production_or_consumption_flag
from {{ ref('int_elexon__b1610_period_mapped') }}
