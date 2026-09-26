{{
    config(
        materialized='incremental',
        group='elexon_facts',
        access='private',
        incremental_strategy='delete+insert',
        unique_key=['bm_unit', 'settlement_date', 'settlement_period'],
        on_schema_change='fail',
        indexes=[
            {
                'columns': ['bm_unit', 'settlement_date', 'settlement_period'],
                'unique': True
            },
            {'columns': ['last_captured_at']}
        ]
    )
}}

-- One row per BM unit and settlement period: the first captured and the
-- latest settlement-run value. Registry evidence is attached in fct_generation.
--
-- Incremental: recompute only unit-periods with a source row captured since
-- the stored watermark minus capture_margin, from all of their source rows.
-- Raw B1610 is insert-only, so this equals a full rebuild while every new row
-- becomes visible within the margin. Otherwise run a full refresh with `+`.

-- Staging is referenced directly in each CTE below, never through a shared
-- CTE: PostgreSQL materialises a CTE used twice, which would read all raw
-- history before the touched-key filter.

{% if is_incremental() %}
with touched as (
    select distinct bm_unit, settlement_date, settlement_period
    from {{ ref('stg_elexon__b1610') }}
    where retrieved_at > {{ capture_watermark(this) }} - {{ capture_margin() }}
),

selected_rows as (
    select source_rows.*
    from {{ ref('stg_elexon__b1610') }} as source_rows
    inner join touched
        on source_rows.bm_unit = touched.bm_unit
        and source_rows.settlement_date = touched.settlement_date
        and source_rows.settlement_period = touched.settlement_period
),
{% else %}
with selected_rows as (
    select * from {{ ref('stg_elexon__b1610') }}
),
{% endif %}

runs as (
    select
        selected_rows.bm_unit,
        selected_rows.national_grid_bm_unit_id,
        selected_rows.settlement_date,
        selected_rows.settlement_period,
        selected_rows.period_start_utc,
        selected_rows.settlement_run_type,
        selected_rows.quantity,
        selected_rows.retrieved_at,
        -- Left join: an unknown run code must fail the staging relationship
        -- test, not disappear here.
        run_codes.run_order
    from selected_rows
    left join {{ ref('elexon_settlement_run_codes') }} as run_codes
        on selected_rows.settlement_run_type = run_codes.settlement_run_code
)

-- One grouped pass: the rows are sorted once by the key, and each ordered
-- aggregate only sorts the few runs inside one period.
-- First captured: earliest retrieved_at, ties broken by run order.
-- Latest: highest run order. An unknown run code (NULL run_order) would sort
-- first here; it never reaches this model because the staging relationship
-- test blocks the run.
select
    bm_unit,
    settlement_date,
    settlement_period,
    -- One value per key; kept out of the GROUP BY to keep the sort key narrow.
    min(period_start_utc) as period_start_utc,
    (array_agg(quantity order by retrieved_at, run_order))[1]
        as first_quantity_mwh,
    (array_agg(settlement_run_type order by retrieved_at, run_order))[1]
        as first_run_code,
    (array_agg(retrieved_at order by retrieved_at, run_order))[1]
        as first_retrieved_at,
    (array_agg(national_grid_bm_unit_id order by retrieved_at, run_order))[1]
        as first_source_national_grid_bm_unit_id,
    (array_agg(quantity order by run_order desc))[1]
        as latest_quantity_mwh,
    (array_agg(settlement_run_type order by run_order desc))[1]
        as latest_run_code,
    (array_agg(retrieved_at order by run_order desc))[1]
        as latest_retrieved_at,
    (array_agg(national_grid_bm_unit_id order by run_order desc))[1]
        as latest_source_national_grid_bm_unit_id,
    max(retrieved_at) as last_captured_at
from runs
group by bm_unit, settlement_date, settlement_period
