select
    raw.extract_id,
    raw.source_index,
    manifest.retrieved_at,
    raw.national_grid_bm_unit,
    raw.elexon_bm_unit,
    raw.eic,
    raw.fuel_type,
    raw.lead_party_name,
    raw.bm_unit_type,
    raw.fpn_flag,
    raw.bm_unit_name,
    raw.lead_party_id,
    nullif(btrim(raw.demand_capacity, E' \t\n\r\f' || chr(11)), '')::numeric
        as demand_capacity_mw,
    nullif(btrim(raw.generation_capacity, E' \t\n\r\f' || chr(11)), '')::numeric
        as generation_capacity_mw,
    raw.production_or_consumption_flag,
    raw.transmission_loss_factor,
    raw.working_day_credit_assessment_import_capability,
    raw.non_working_day_credit_assessment_import_capability,
    raw.working_day_credit_assessment_export_capability,
    raw.non_working_day_credit_assessment_export_capability,
    raw.credit_qualifying_status,
    raw.demand_in_production_flag,
    raw.gsp_group_id,
    raw.gsp_group_name,
    raw.interconnector_id
from {{ source('elexon', 'elexon_bm_units') }} as raw
inner join {{ source('elexon', 'elexon_bm_units_extracts') }} as manifest
    on raw.extract_id = manifest.extract_id
