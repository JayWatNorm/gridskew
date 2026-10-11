{{ config(group='analysis', access='private') }}

-- The two identifiers of every unit in the latest registry extract, without
-- the eics array, so that a unit test can mock the registry.

select
    national_grid_bm_unit,
    elexon_bm_unit
from {{ ref('int_elexon__bm_units_current') }}
