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
-- because Q3 and Q12 leave them out. Every section covers all stored
-- history; energy is the sum of absolute MWh of the latest settlement run.
-- Reads counts and summed absolute energy only. It reads the full fact
-- views, so it takes minutes; run it by hand.

with metered as (
    select
        mapping_status,
        current_fuel_type is not null as has_fuel_type,
        latest_quantity_mwh
    from {{ ref('fct_generation') }}
),

whole_source as (
    select
        1 as section,
        'all metered unit-periods' as breakdown,
        case
            when mapping_status = 'mapped' and has_fuel_type
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
        generation.current_fuel_type is not null as has_fuel_type,
        generation.latest_quantity_mwh,
        commitments.pn_mwh
    from {{ ref('fct_generation') }} as generation
    inner join {{ ref('fct_commitments') }} as commitments
        on commitments.national_grid_bm_unit = generation.mapped_national_grid_bm_unit
        and commitments.period_start_utc = generation.period_start_utc
    where generation.mapping_status = 'mapped'
),

matched_with_pn as (
    select
        2 as section,
        'mapped metered unit-periods with a PN' as breakdown,
        case when has_fuel_type then 'fuel type known (the cohort)'
            else 'no fuel type' end as item,
        count(*) as unit_periods,
        round(sum(abs(latest_quantity_mwh)), 1) as abs_metered_mwh,
        round(sum(abs(pn_mwh)), 1) as abs_pn_mwh
    from matched
    group by has_fuel_type
),

cohort_table as (
    select
        3 as section,
        'cohort table by determinability' as breakdown,
        shortfall.determinability
            || case when shortfall.determinability = 'determinable'
                and shortfall.pn_mwh is null then ' without a PN' else '' end
            as item,
        count(*) as unit_periods,
        round(sum(abs(shortfall.metered_mwh)), 1) as abs_metered_mwh,
        round(sum(abs(shortfall.pn_mwh)), 1) as abs_pn_mwh
    from {{ ref('int_shortfall_by_unit_period') }} as shortfall
    inner join {{ ref('int_elexon__bm_unit_cohort') }} as cohort
        on shortfall.national_grid_bm_unit = cohort.national_grid_bm_unit
    group by 3
)

select * from whole_source
union all
select * from matched_with_pn
union all
select * from cohort_table
order by section, abs_metered_mwh desc nulls last
