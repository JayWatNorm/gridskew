{{ config(group='analysis') }}

-- Q5: should the forecast have known? Reading rule (research questions):
-- the share of unexplained shortfall MWh in half-hours with a known outage,
-- split planned and unplanned; no verdict, a measured share. A half-hour has
-- a known outage when int_remit__known_outage_periods lists its unit: an
-- outage of the unit was already published, in force and Active at the
-- half-hour's start. "Known" means published in REMIT; it does not say the
-- forecaster read it.
--
-- Population: determinable cohort unit-periods of int_shortfall_by_unit_period,
-- with or without a PN (the table has no placeholder rows; those are in
-- int_elexon__instruction_intervals). The measure is the
-- positive part of unexplained shortfall (under-delivery). Section 1 is the
-- headline by known class with a total; section 2 splits it by fuel group;
-- section 3 is coverage. Run once, by hand, after the rule is recorded.

with cohort_periods as (
    select
        shortfall.national_grid_bm_unit,
        shortfall.period_start_utc,
        shortfall.determinability,
        cohort.fuel_group,
        greatest(shortfall.unexplained_shortfall_mwh, 0) as positive_shortfall_mwh
    from {{ ref('int_shortfall_by_unit_period') }} as shortfall
    inner join {{ ref('int_elexon__bm_unit_cohort') }} as cohort
        on shortfall.national_grid_bm_unit = cohort.national_grid_bm_unit
),

classified as (
    select
        cohort_periods.fuel_group,
        coalesce(known.known_class, 'none') as known_class,
        cohort_periods.positive_shortfall_mwh
    from cohort_periods
    left join {{ ref('int_remit__known_outage_periods') }} as known
        on cohort_periods.national_grid_bm_unit = known.national_grid_bm_unit
        and cohort_periods.period_start_utc = known.period_start_utc
    where cohort_periods.determinability = 'determinable'
),

total as (
    select sum(positive_shortfall_mwh) as positive_shortfall_mwh
    from classified
),

headline as (
    select
        1 as section,
        null::text as fuel_group,
        known_class as item,
        count(*) as unit_periods,
        round(sum(positive_shortfall_mwh), 1) as positive_shortfall_mwh,
        round(100 * sum(positive_shortfall_mwh) / nullif((select positive_shortfall_mwh from total), 0), 1)
            as share_of_positive_shortfall_pct
    from classified
    group by known_class

    union all

    select
        1,
        null,
        'total',
        count(*),
        round(sum(positive_shortfall_mwh), 1),
        round(100 * sum(positive_shortfall_mwh) / nullif((select positive_shortfall_mwh from total), 0), 1)
    from classified
),

by_fuel_group as (
    select
        2 as section,
        fuel_group,
        known_class as item,
        count(*) as unit_periods,
        round(sum(positive_shortfall_mwh), 1) as positive_shortfall_mwh,
        round(100 * sum(positive_shortfall_mwh) / nullif(sum(sum(positive_shortfall_mwh)) over (partition by fuel_group), 0), 1)
            as share_of_positive_shortfall_pct
    from classified
    group by fuel_group, known_class
),

coverage as (
    select
        3 as section,
        null::text as fuel_group,
        'cohort unit-periods: ' || determinability as item,
        count(*) as unit_periods,
        round(sum(positive_shortfall_mwh) filter (where determinability = 'determinable'), 1)
            as positive_shortfall_mwh,
        null::numeric as share_of_positive_shortfall_pct
    from cohort_periods
    where determinability is not null
    group by determinability

    union all

    select
        3,
        null,
        'determinable cohort unit-periods with no period_start_utc (cannot join)',
        count(*),
        null,
        null
    from cohort_periods
    where determinability = 'determinable'
        and period_start_utc is null
)

select * from headline
union all
select * from by_fuel_group
union all
select * from coverage
order by section, fuel_group, item
