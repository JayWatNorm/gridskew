{{ config(materialized='view', group='elexon_facts') }}

-- One row per PN National Grid BM unit and settlement period: the persisted
-- period MWh and coverage plus current registry mapping and evidence. A view,
-- so a registry change shows at once without rewriting any period row.

with mapped as (
    select
        periods.*,
        -- The registry is unique on National Grid ID (blocking test
        -- upstream), so this join cannot multiply periods.
        case
            when registry.national_grid_bm_unit is null then 'unmapped'
            when periods.has_conflicting_source_bm_units then 'conflict'
            when periods.source_bm_unit is not null
                and periods.source_bm_unit
                is distinct from registry.elexon_bm_unit
                then 'conflict'
            else 'mapped'
        end as mapping_status,
        registry.elexon_bm_unit as candidate_elexon_bm_unit,
        registry.fuel_type as candidate_fuel_type,
        registry.bm_unit_type as candidate_bm_unit_type,
        registry.production_or_consumption_flag
            as candidate_production_or_consumption_flag
    from {{ ref('int_elexon__pn_period_mwh') }} as periods
    left join {{ ref('int_elexon__bm_units_current') }} as registry
        on periods.national_grid_bm_unit = registry.national_grid_bm_unit
)

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
    mapping_status,
    case when mapping_status = 'mapped'
        then candidate_elexon_bm_unit
    end as mapped_elexon_bm_unit,
    case when mapping_status = 'mapped'
        then candidate_fuel_type
    end as current_fuel_type,
    case when mapping_status = 'mapped'
        then candidate_bm_unit_type
    end as current_bm_unit_type,
    case when mapping_status = 'mapped'
        then candidate_production_or_consumption_flag
    end as current_production_or_consumption_flag
from mapped
