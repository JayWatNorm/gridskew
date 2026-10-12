-- Warning: int_shortfall_by_unit_period needs a full refresh because another
-- unit started or stopped sharing a cohort unit's Elexon ID. While the ID is
-- shared, fct_generation reports its metered rows as ambiguous and the
-- shortfall table must hold no metered value for the unit. The incremental
-- run cannot see the change in the unit's own registry row, so stored rows
-- keep the mapping they were built with. Returns one row per unit whose
-- stored rows disagree with the registry today: metered values kept although
-- the ID is now shared, or missing although fct_generation now maps a metered
-- row to the unit. It clears once a full refresh rebuilds those rows.
-- Captures newer than the table's watermark minus capture_margin are left
-- out: the next incremental run recomputes them anyway. Reads every stored
-- row without a metered value once, and B1610 rows through their unique
-- index for those rows only.

{{ config(group='analysis', severity='warn') }}

{% set shortfall_watermark = capture_watermark(ref('int_shortfall_by_unit_period')) %}

with shared_ids as (
    select elexon_bm_unit
    from {{ ref('int_elexon__bm_units_current') }}
    where elexon_bm_unit is not null
    group by elexon_bm_unit
    having count(*) > 1
),

cohort as (
    select
        cohort.national_grid_bm_unit,
        cohort.elexon_bm_unit,
        shared_ids.elexon_bm_unit is not null as elexon_id_shared_now
    from {{ ref('int_elexon__bm_unit_cohort') }} as cohort
    left join shared_ids
        on cohort.elexon_bm_unit = shared_ids.elexon_bm_unit
    where cohort.elexon_bm_unit is not null
),

metered_values_kept_after_sharing_started as (
    select
        cohort.national_grid_bm_unit,
        count(*) as stale_periods
    from cohort
    inner join {{ ref('int_shortfall_by_unit_period') }} as stored
        on stored.national_grid_bm_unit = cohort.national_grid_bm_unit
    where cohort.elexon_id_shared_now
        and stored.metered_row_count > 0
    group by cohort.national_grid_bm_unit
),

metered_values_missing_after_sharing_stopped as (
    select
        cohort.national_grid_bm_unit,
        count(*) as stale_periods
    from cohort
    inner join {{ ref('int_shortfall_by_unit_period') }} as stored
        on stored.national_grid_bm_unit = cohort.national_grid_bm_unit
    inner join {{ ref('fct_generation') }} as generation
        on generation.bm_unit = cohort.elexon_bm_unit
        and generation.settlement_date = stored.settlement_date
        and generation.settlement_period = stored.settlement_period
        and generation.period_start_utc = stored.period_start_utc
    where not cohort.elexon_id_shared_now
        and stored.metered_row_count = 0
        and generation.mapping_status = 'mapped'
        and generation.mapped_national_grid_bm_unit = cohort.national_grid_bm_unit
        and generation.latest_retrieved_at
            <= {{ shortfall_watermark }} - {{ capture_margin() }}
    group by cohort.national_grid_bm_unit
),

stale_units as (
    select national_grid_bm_unit, stale_periods
    from metered_values_kept_after_sharing_started

    union all

    select national_grid_bm_unit, stale_periods
    from metered_values_missing_after_sharing_stopped
)

select
    cohort.national_grid_bm_unit,
    cohort.elexon_bm_unit,
    cohort.elexon_id_shared_now,
    stale_units.stale_periods
from stale_units
inner join cohort
    on stale_units.national_grid_bm_unit = cohort.national_grid_bm_unit
