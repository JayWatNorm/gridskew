{{ config(materialized='table', group='analysis') }}

-- How much metered generation the cohort holds. Generation is the positive
-- metered energy of the latest settlement run captured; the cohort is every
-- mapped unit with a fuel type. Section 1 splits generation by the kind of
-- BM unit, read from its Elexon ID prefix (T_ transmission-connected, E_
-- embedded, I_ interconnector trader, 2__ supplier, V__ virtual). Section 2
-- follows the selection from every metered unit-period to the Q3 and Q12
-- population. Up to analysis_read_until; built by hand with
-- agg_delivery_by_fuel_group.

{% set read_until = analysis_read_until() %}

with metered as (
    select
        case
            when generation.bm_unit like 'T\_%' then 'T_'
            when generation.bm_unit like 'E\_%' then 'E_'
            when generation.bm_unit like 'I\_%' then 'I_'
            when generation.bm_unit like '2\_\_%' then '2__'
            when generation.bm_unit like 'V\_\_%' then 'V__'
            else 'other'
        end as unit_kind,
        generation.mapping_status = 'mapped' as is_mapped,
        cohort.national_grid_bm_unit is not null as is_cohort,
        greatest(generation.latest_quantity_mwh, 0) as generation_mwh
    from {{ ref('fct_generation') }} as generation
    left join {{ ref('int_elexon__bm_unit_cohort') }} as cohort
        on generation.mapped_national_grid_bm_unit = cohort.national_grid_bm_unit
{% if read_until is not none %}
    where coalesce(generation.period_start_utc, generation.settlement_date::timestamp at time zone 'Europe/London')
            <= timestamptz '{{ read_until }}'
{% endif %}
),

by_kind as (
    select
        unit_kind as item,
        count(*) as unit_periods,
        sum(generation_mwh) as generation_mwh,
        count(*) filter (where is_cohort) as cohort_unit_periods,
        coalesce(sum(generation_mwh) filter (where is_cohort), 0) as cohort_generation_mwh
    from metered
    group by unit_kind
),

kind_totals as (
    select
        'all' as item,
        sum(unit_periods)::bigint as unit_periods,
        sum(generation_mwh) as generation_mwh,
        sum(cohort_unit_periods)::bigint as cohort_unit_periods,
        sum(cohort_generation_mwh) as cohort_generation_mwh
    from by_kind

    union all

    select
        'all except I_' as item,
        sum(unit_periods)::bigint,
        sum(generation_mwh),
        sum(cohort_unit_periods)::bigint,
        sum(cohort_generation_mwh)
    from by_kind
    where item <> 'I_'
),

generation_by_kind as (
    select
        1 as section,
        item,
        unit_periods,
        round(generation_mwh, 1) as generation_mwh,
        cohort_unit_periods,
        round(cohort_generation_mwh, 1) as cohort_generation_mwh,
        round(cohort_generation_mwh / nullif(generation_mwh, 0), 6) as cohort_generation_share
    from (
        select * from by_kind
        union all
        select * from kind_totals
    ) as kinds
),

q3_q12_population as (
    select greatest(shortfall.metered_mwh, 0) as generation_mwh
    from {{ ref('int_shortfall_by_unit_period') }} as shortfall
    inner join {{ ref('int_elexon__bm_unit_cohort') }} as cohort
        on shortfall.national_grid_bm_unit = cohort.national_grid_bm_unit
    where shortfall.determinability = 'determinable'
        and shortfall.pn_mwh is not null
{% if read_until is not none %}
        and coalesce(shortfall.period_start_utc, shortfall.settlement_date::timestamp at time zone 'Europe/London')
            <= timestamptz '{{ read_until }}'
{% endif %}
),

selection_flow as (
    select 'all metered' as item, count(*) as unit_periods, sum(generation_mwh) as generation_mwh
    from metered

    union all

    select 'mapped', count(*), sum(generation_mwh)
    from metered
    where is_mapped

    union all

    select 'cohort', count(*), sum(generation_mwh)
    from metered
    where is_cohort

    union all

    select 'cohort, determinable with a PN', count(*), sum(generation_mwh)
    from q3_q12_population
)

select
    section,
    item,
    unit_periods,
    generation_mwh,
    cohort_unit_periods,
    cohort_generation_mwh,
    cohort_generation_share,
{% if read_until is not none %}
    timestamptz '{{ read_until }}' as analysis_read_until
{% else %}
    null::timestamptz as analysis_read_until
{% endif %}
from generation_by_kind

union all

select
    2,
    item,
    unit_periods,
    round(coalesce(generation_mwh, 0), 1),
    null::bigint,
    null::numeric,
    null::numeric,
{% if read_until is not none %}
    timestamptz '{{ read_until }}'
{% else %}
    null::timestamptz
{% endif %}
from selection_flow
