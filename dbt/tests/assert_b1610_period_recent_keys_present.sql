-- Nightly missing-row check: every B1610 key with a source row captured in
-- the last build's window (watermark minus capture_margin, up to the
-- watermark) has a period row. Returns the missing keys. Cheap: the literal
-- watermark lets it read only recent source rows through the index. A row
-- committed between the build and this test can fail it once; the next build
-- picks that row up.

{{ config(group='elexon_facts') }}

{% set watermark = capture_watermark(ref('int_elexon__b1610_period')) %}

with recent_keys as (
    select distinct bm_unit, settlement_date, settlement_period
    from {{ ref('stg_elexon__b1610') }}
    where retrieved_at > {{ watermark }} - {{ capture_margin() }}
        and retrieved_at <= {{ watermark }}
)

select recent_keys.*
from recent_keys
left join {{ ref('int_elexon__b1610_period') }} as periods
    on recent_keys.bm_unit = periods.bm_unit
    and recent_keys.settlement_date = periods.settlement_date
    and recent_keys.settlement_period = periods.settlement_period
where periods.bm_unit is null
