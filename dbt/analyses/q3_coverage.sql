{{ config(group='analysis') }}

-- Coverage for Q3 and Q12: what the cohort table holds against the whole
-- metered source, so a result can say how much of the source it represents.
-- Section 1 counts every metered unit-period in fct_generation, with its
-- energy, by mapping status and whether the mapped unit has a fuel type;
-- no PN join, so metered periods with no PN and unmapped, conflicting or
-- ambiguous periods are all there. Section 2 counts the mapped periods that
-- also have a PN, the pairs Q3 can compare, split by fuel label. Section 3
-- splits the cohort table by determinability, with determinable periods
-- that have no PN (the acceptances cover the whole half-hour) shown apart,
-- because Q3 and Q12 leave them out. Section 4 splits the Q3 and Q12
-- population by the settlement run its metered value comes from: a later
-- run replaces an earlier one, so a re-read can give different numbers.
-- Every section covers stored history up to analysis_read_until (all of it
-- when unset); energy is the sum of absolute MWh of the latest settlement
-- run captured. Reads counts and summed absolute energy only. It reads the
-- full fact views, so it takes minutes; run it by hand.

{% set read_until = analysis_read_until() %}

with metered as (
    select
        generation.mapping_status,
        cohort.national_grid_bm_unit is not null as is_cohort,
        generation.latest_quantity_mwh
    from {{ ref('fct_generation') }} as generation
    left join {{ ref('int_elexon__bm_unit_cohort') }} as cohort
        on generation.mapped_national_grid_bm_unit = cohort.national_grid_bm_unit
{% if read_until is not none %}
    where coalesce(generation.period_start_utc, generation.settlement_date::timestamp at time zone 'Europe/London')
            <= timestamptz '{{ read_until }}'
{% endif %}
),

whole_source as (
    select
        1 as section,
        'all metered unit-periods' as breakdown,
        case
            when is_cohort
                then 'mapped, fuel type known (the cohort)'
            when mapping_status = 'mapped'
                then 'mapped, no fuel type'
            else mapping_status
        end as item,
        count(*) as unit_periods,
        round(sum(abs(latest_quantity_mwh)), 1) as abs_metered_mwh,
        null::numeric as abs_pn_mwh
    from metered
    group by 3
),

matched as (
    select
        cohort.national_grid_bm_unit is not null as is_cohort,
        generation.latest_quantity_mwh,
        commitments.pn_mwh
    from {{ ref('fct_generation') }} as generation
    inner join {{ ref('fct_commitments') }} as commitments
        on commitments.national_grid_bm_unit = generation.mapped_national_grid_bm_unit
        and commitments.period_start_utc = generation.period_start_utc
    left join {{ ref('int_elexon__bm_unit_cohort') }} as cohort
        on generation.mapped_national_grid_bm_unit = cohort.national_grid_bm_unit
    where generation.mapping_status = 'mapped'
{% if read_until is not none %}
        and coalesce(generation.period_start_utc, generation.settlement_date::timestamp at time zone 'Europe/London')
            <= timestamptz '{{ read_until }}'
{% endif %}
),

matched_with_pn as (
    select
        2 as section,
        'mapped metered unit-periods with a PN' as breakdown,
        case when is_cohort then 'fuel type known (the cohort)'
            else 'no fuel type' end as item,
        count(*) as unit_periods,
        round(sum(abs(latest_quantity_mwh)), 1) as abs_metered_mwh,
        round(sum(abs(pn_mwh)), 1) as abs_pn_mwh
    from matched
    group by is_cohort
),

cohort_periods as (
    select
        shortfall.determinability,
        shortfall.pn_mwh,
        shortfall.metered_mwh,
        shortfall.metered_run_code
    from {{ ref('int_shortfall_by_unit_period') }} as shortfall
    inner join {{ ref('int_elexon__bm_unit_cohort') }} as cohort
        on shortfall.national_grid_bm_unit = cohort.national_grid_bm_unit
{% if read_until is not none %}
    where coalesce(shortfall.period_start_utc, shortfall.settlement_date::timestamp at time zone 'Europe/London')
            <= timestamptz '{{ read_until }}'
{% endif %}
),

cohort_table as (
    select
        3 as section,
        'cohort table by determinability' as breakdown,
        determinability
            || case when determinability = 'determinable'
                and pn_mwh is null then ' without a PN' else '' end
            as item,
        count(*) as unit_periods,
        round(sum(abs(metered_mwh)), 1) as abs_metered_mwh,
        round(sum(abs(pn_mwh)), 1) as abs_pn_mwh
    from cohort_periods
    group by 3
),

population_by_run as (
    select
        4 as section,
        'Q3 and Q12 population by metered run' as breakdown,
        metered_run_code as item,
        count(*) as unit_periods,
        round(sum(abs(metered_mwh)), 1) as abs_metered_mwh,
        round(sum(abs(pn_mwh)), 1) as abs_pn_mwh
    from cohort_periods
    where determinability = 'determinable'
        and pn_mwh is not null
    group by metered_run_code
)

select * from whole_source
union all
select * from matched_with_pn
union all
select * from cohort_table
union all
select * from population_by_run
order by section, abs_metered_mwh desc nulls last
