{{ config(group='analysis', access='private') }}

-- The analysis cohort: every registry unit with a published fuel type, with
-- its fuel group from the seed. One row per National Grid BM unit. A unit
-- without a fuel type has no physical baseline and is not here. This is the
-- one place the cohort rule lives; the shortfall table and its tests read it.
-- Array-free on purpose, so unit tests can mock it.

select
    registry.national_grid_bm_unit,
    registry.elexon_bm_unit,
    registry.fuel_type,
    coalesce(fuel_codes.code_group, 'unclassified') as fuel_group
from {{ ref('int_elexon__bm_units_current') }} as registry
left join {{ ref('elexon_fuel_codes') }} as fuel_codes
    on registry.fuel_type = fuel_codes.fuel_code
where registry.fuel_type is not null
