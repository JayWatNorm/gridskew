{{ config(group='analysis') }}

-- Q12: how much of the deviation from PN was instructed rather than
-- unexplained? Reading rule (research questions): describe, per fuel group,
-- the share of the deviation from PN that the acceptances in force asked
-- for. deviation_from_pn_mwh = instructed_deviation_mwh +
-- unexplained_shortfall_mwh in every determinable period with a PN, so the
-- two parts are reported as sums of absolute energy and the instructed part
-- as a share of their total. A period determinable without a PN (its
-- acceptances cover the whole half-hour) has a shortfall but no deviation to
-- split, so it is left out here and counted in q3_coverage.sql. Instructed energy is never added up across
-- overlapping acceptances (decision 020): the interval model keeps the
-- acceptance in force for each minute only.

with determinable as (
    select
        cohort.fuel_group,
        shortfall.instruction_status,
        shortfall.deviation_from_pn_mwh,
        shortfall.instructed_deviation_mwh,
        shortfall.unexplained_shortfall_mwh
    from {{ ref('int_shortfall_by_unit_period') }} as shortfall
    inner join {{ ref('int_elexon__bm_unit_cohort') }} as cohort
        on shortfall.national_grid_bm_unit = cohort.national_grid_bm_unit
    where shortfall.determinability = 'determinable'
        and shortfall.pn_mwh is not null
)

select
    fuel_group,
    count(*) as unit_periods,
    count(*) filter (where instruction_status <> 'uninstructed')
        as instructed_periods,
    round(
        count(*) filter (where instruction_status <> 'uninstructed')::numeric
        / count(*),
        6
    ) as instructed_period_share,
    round(sum(abs(deviation_from_pn_mwh)), 1) as abs_deviation_mwh,
    round(sum(abs(instructed_deviation_mwh)), 1) as abs_instructed_deviation_mwh,
    round(sum(abs(unexplained_shortfall_mwh)), 1) as abs_unexplained_shortfall_mwh,
    round(
        sum(abs(instructed_deviation_mwh))
        / nullif(
            sum(abs(instructed_deviation_mwh)) + sum(abs(unexplained_shortfall_mwh)),
            0
        ),
        6
    ) as instructed_share_of_abs_deviation,
    round(
        percentile_cont(0.5) within group (order by unexplained_shortfall_mwh)::numeric,
        3
    ) as median_unexplained_shortfall_mwh
from determinable
group by fuel_group
order by abs_deviation_mwh desc
