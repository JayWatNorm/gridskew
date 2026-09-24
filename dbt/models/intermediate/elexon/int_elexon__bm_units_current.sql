with latest_extract as (
    select extract_id
    from {{ source('elexon', 'elexon_bm_units_extracts') }}
    order by retrieved_at desc, extract_id desc
    limit 1
),

selected as (
    select source_rows.*
    from {{ ref('stg_elexon__bm_units') }} as source_rows
    inner join latest_extract
        on source_rows.extract_id = latest_extract.extract_id
)

select
    extract_id,
    retrieved_at,
    national_grid_bm_unit,
    elexon_bm_unit,
    coalesce(
        array_agg(distinct eic order by eic) filter (
            where eic is not null and btrim(eic) <> ''
        ),
        array[]::text[]
    ) as eics,
    fuel_type,
    lead_party_name,
    bm_unit_type,
    fpn_flag,
    bm_unit_name,
    lead_party_id,
    demand_capacity_mw,
    generation_capacity_mw,
    production_or_consumption_flag,
    transmission_loss_factor,
    working_day_credit_assessment_import_capability,
    non_working_day_credit_assessment_import_capability,
    working_day_credit_assessment_export_capability,
    non_working_day_credit_assessment_export_capability,
    credit_qualifying_status,
    demand_in_production_flag,
    gsp_group_id,
    gsp_group_name,
    interconnector_id,
    count(*) as source_row_count
from selected
group by
    extract_id,
    retrieved_at,
    national_grid_bm_unit,
    elexon_bm_unit,
    fuel_type,
    lead_party_name,
    bm_unit_type,
    fpn_flag,
    bm_unit_name,
    lead_party_id,
    demand_capacity_mw,
    generation_capacity_mw,
    production_or_consumption_flag,
    transmission_loss_factor,
    working_day_credit_assessment_import_capability,
    non_working_day_credit_assessment_import_capability,
    working_day_credit_assessment_export_capability,
    non_working_day_credit_assessment_export_capability,
    credit_qualifying_status,
    demand_in_production_flag,
    gsp_group_id,
    gsp_group_name,
    interconnector_id
