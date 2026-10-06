{{ config(materialized='view', group='elexon_facts') }}

-- One row per B1610 BM unit and settlement period: the persisted period values
-- plus current registry mapping and evidence. A view, so a registry change
-- shows at once without rewriting any period row.

with registry_candidates as (
    -- One row per Elexon ID, so the join below cannot multiply periods.
    -- Attributes are only used when exactly one candidate exists.
    select
        elexon_bm_unit,
        count(*) as candidate_count,
        min(national_grid_bm_unit) as national_grid_bm_unit,
        min(fuel_type) as fuel_type,
        min(bm_unit_type) as bm_unit_type,
        min(production_or_consumption_flag) as production_or_consumption_flag
    from {{ ref('int_elexon__bm_units_current') }}
    where elexon_bm_unit is not null
    group by elexon_bm_unit
),

mapped as (
    select
        periods.*,
        case
            when registry.candidate_count is null then 'unmapped'
            when registry.candidate_count > 1 then 'ambiguous'
            -- Letter case is ignored: the registry has changed the case of a
            -- National Grid ID while the unit stayed the same. Any other
            -- difference stays a conflict.
            when lower(periods.first_source_national_grid_bm_unit_id)
                <> lower(registry.national_grid_bm_unit)
                or lower(periods.latest_source_national_grid_bm_unit_id)
                <> lower(registry.national_grid_bm_unit)
                then 'conflict'
            else 'mapped'
        end as mapping_status,
        registry.national_grid_bm_unit as candidate_national_grid_bm_unit,
        registry.fuel_type as candidate_fuel_type,
        registry.bm_unit_type as candidate_bm_unit_type,
        registry.production_or_consumption_flag
            as candidate_production_or_consumption_flag
    from {{ ref('int_elexon__b1610_period') }} as periods
    left join registry_candidates as registry
        on periods.bm_unit = registry.elexon_bm_unit
)

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
    mapping_status,
    case when mapping_status = 'mapped'
        then candidate_national_grid_bm_unit
    end as mapped_national_grid_bm_unit,
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
