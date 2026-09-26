-- Nightly missing-row check: every PN key with a capture in the last build's
-- window (watermark minus capture_margin, up to the watermark) has a period
-- row. Returns the missing keys. Cheap: the literal watermark lets it read
-- only recent source rows through the index. A row committed between the
-- build and this test can fail it once; the next build picks that row up.

{{ config(group='elexon_facts') }}

{% set watermark = capture_watermark(ref('int_elexon__pn_period_mwh')) %}

with recent_keys as (
    select distinct national_grid_bm_unit, settlement_date, settlement_period
    from {{ ref('stg_elexon__pn') }}
    where retrieved_at > {{ watermark }} - {{ capture_margin() }}
        and retrieved_at <= {{ watermark }}
)

select recent_keys.*
from recent_keys
left join {{ ref('int_elexon__pn_period_mwh') }} as periods
    on recent_keys.national_grid_bm_unit = periods.national_grid_bm_unit
    and recent_keys.settlement_date = periods.settlement_date
    and recent_keys.settlement_period = periods.settlement_period
where periods.national_grid_bm_unit is null
