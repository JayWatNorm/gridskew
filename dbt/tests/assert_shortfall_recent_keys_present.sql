-- Nightly missing-row check: every cohort PN key captured in the last build's
-- window (watermark minus capture_margin, up to the watermark) has a shortfall
-- row. Returns the missing keys. The cohort join keeps the read to the units
-- the table holds. A row committed between the build and this test can fail it
-- once; the next build picks that row up.

{{ config(group='analysis') }}

{% set watermark = capture_watermark(ref('int_shortfall_by_unit_period')) %}

with cohort as (
    select national_grid_bm_unit
    from {{ ref('int_elexon__bm_unit_cohort') }}
),

recent_keys as (
    select distinct
        commitments.national_grid_bm_unit,
        commitments.settlement_date,
        commitments.settlement_period
    from {{ ref('fct_commitments') }} as commitments
    inner join cohort
        on commitments.national_grid_bm_unit = cohort.national_grid_bm_unit
    where commitments.pn_retrieved_at > {{ watermark }} - {{ capture_margin() }}
        and commitments.pn_retrieved_at <= {{ watermark }}
)

select recent_keys.*
from recent_keys
left join {{ ref('int_shortfall_by_unit_period') }} as shortfall
    on recent_keys.national_grid_bm_unit = shortfall.national_grid_bm_unit
    and recent_keys.settlement_date = shortfall.settlement_date
    and recent_keys.settlement_period = shortfall.settlement_period
where shortfall.national_grid_bm_unit is null
