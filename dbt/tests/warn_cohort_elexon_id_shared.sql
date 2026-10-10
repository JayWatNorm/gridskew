-- Warning: a cohort unit whose Elexon ID the registry also gives to another
-- unit. fct_generation then reports its metered rows as ambiguous, and the
-- shortfall table cannot see that from the unit's own registry row, so the
-- shortfall tables need a full refresh when this starts or stops. Returns
-- the units concerned. Reads the registry only.

{{ config(group='analysis', severity='warn') }}

with shared_ids as (
    select elexon_bm_unit
    from {{ ref('int_elexon__bm_units_current') }}
    where elexon_bm_unit is not null
    group by elexon_bm_unit
    having count(*) > 1
)

select cohort.national_grid_bm_unit, cohort.elexon_bm_unit
from {{ ref('int_elexon__bm_unit_cohort') }} as cohort
inner join shared_ids
    on cohort.elexon_bm_unit = shared_ids.elexon_bm_unit
