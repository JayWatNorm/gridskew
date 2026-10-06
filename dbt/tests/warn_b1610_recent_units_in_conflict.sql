-- Nightly warning: BM units whose recently captured B1610 periods carry a
-- National Grid ID that disagrees with the registry, by the rule
-- fct_generation uses (one registry unit for the Elexon ID, letter case
-- ignored). A registry rename shows here within days: periods captured across
-- the rename hold the old ID from the first run and the new ID from a later
-- one. Returns one row per unit and never blocks the run. Cheap: the literal
-- watermark lets it read only recent period rows through the index.

{{ config(group='elexon_facts', severity='warn') }}

{% set watermark = capture_watermark(ref('int_elexon__b1610_period')) %}

with recent_periods as (
    select
        bm_unit,
        first_source_national_grid_bm_unit_id,
        latest_source_national_grid_bm_unit_id
    from {{ ref('int_elexon__b1610_period') }}
    where last_captured_at > {{ watermark }} - {{ capture_margin() }}
),

single_registry_units as (
    select
        elexon_bm_unit,
        min(national_grid_bm_unit) as national_grid_bm_unit
    from {{ ref('int_elexon__bm_units_current') }}
    where elexon_bm_unit is not null
    group by elexon_bm_unit
    having count(*) = 1
)

select
    recent_periods.bm_unit,
    registry.national_grid_bm_unit as registry_national_grid_bm_unit,
    min(recent_periods.first_source_national_grid_bm_unit_id)
        as first_source_national_grid_bm_unit_id,
    min(recent_periods.latest_source_national_grid_bm_unit_id)
        as latest_source_national_grid_bm_unit_id,
    count(*) as recent_periods_in_conflict
from recent_periods
inner join single_registry_units as registry
    on recent_periods.bm_unit = registry.elexon_bm_unit
where lower(recent_periods.first_source_national_grid_bm_unit_id)
    <> lower(registry.national_grid_bm_unit)
    or lower(recent_periods.latest_source_national_grid_bm_unit_id)
    <> lower(registry.national_grid_bm_unit)
group by recent_periods.bm_unit, registry.national_grid_bm_unit
