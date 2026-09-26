-- Fan-out check: the PN period table must have exactly one row per
-- distinct staging key. More rows means periods were duplicated; fewer
-- means rows were dropped. Returns one row when the counts differ.
-- Full-population check: run with every full refresh, excluded from normal
-- incremental runs (--exclude tag:full_population). In the group because the
-- intermediate is private to it.

{{ config(group='elexon_facts', tags=['full_population']) }}

with source_keys as (
    select count(*) as key_count
    from (
        select distinct national_grid_bm_unit, settlement_date, settlement_period
        from {{ ref('stg_elexon__pn') }}
    ) as keys
),

period_rows as (
    select count(*) as row_count
    from {{ ref('int_elexon__pn_period_mwh') }}
)

select source_keys.key_count, period_rows.row_count
from source_keys
cross join period_rows
where source_keys.key_count <> period_rows.row_count
