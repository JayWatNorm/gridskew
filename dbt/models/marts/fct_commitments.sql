{{ config(materialized='view', group='elexon_facts') }}

-- One row per PN National Grid BM unit and settlement period: the persisted
-- period MWh and coverage plus current registry mapping and evidence,
-- published from int_elexon__pn_period_mapped, where the mapping is made.
--
-- Written without WITH: PostgreSQL flattens a view like this one into the
-- joins of the query that reads it (within join_collapse_limit), so a join
-- on the period key reaches the period table's unique index. A view with a
-- WITH list is not flattened, and a join reads all of it.

select
    national_grid_bm_unit,
    settlement_date,
    settlement_period,
    period_start_utc,
    pn_mwh,
    coverage_status,
    segment_count,
    pn_retrieved_at,
    source_bm_unit,
    last_captured_at,
    mapping_status,
    mapped_elexon_bm_unit,
    current_fuel_type,
    current_bm_unit_type,
    current_production_or_consumption_flag
from {{ ref('int_elexon__pn_period_mapped') }}
