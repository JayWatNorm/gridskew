{{ config(group='analysis') }}

-- Q3: how far is metered output from the notified position, by fuel group?
-- Reading rule (research questions): describe only, the median and
-- interquartile range of deviation_from_pn_mwh per fuel group, with n, the
-- date range and coverage beside it. Every figure is conditional on the
-- cohort (registry units with a fuel type); q3_coverage.sql reports what the
-- cohort leaves out and the settlement runs behind the metered values. Reads
-- determinable periods with a PN: a period whose acceptances cover the whole
-- half-hour is determinable without one, and has no deviation from PN to
-- describe.

{% set read_until = var('analysis_read_until') %}

-- The cohort view gives membership and the fuel group as the registry has
-- them today.
with determinable as (
    select
        cohort.fuel_group,
        shortfall.settlement_date,
        shortfall.national_grid_bm_unit,
        shortfall.deviation_from_pn_mwh,
        shortfall.metered_mwh,
        shortfall.pn_mwh
    from {{ ref('int_shortfall_by_unit_period') }} as shortfall
    inner join {{ ref('int_elexon__bm_unit_cohort') }} as cohort
        on shortfall.national_grid_bm_unit = cohort.national_grid_bm_unit
    where shortfall.determinability = 'determinable'
        and shortfall.pn_mwh is not null
{% if read_until is not none %}
        and coalesce(shortfall.period_start_utc, shortfall.settlement_date::timestamp at time zone 'Europe/London')
            <= timestamptz '{{ read_until }}'
{% endif %}
)

select
    fuel_group,
    count(*) as unit_periods,
    count(distinct national_grid_bm_unit) as units,
    min(settlement_date) as first_date,
    max(settlement_date) as last_date,
    round(
        percentile_cont(0.5) within group (order by deviation_from_pn_mwh)::numeric,
        3
    ) as median_deviation_mwh,
    round(
        percentile_cont(0.25) within group (order by deviation_from_pn_mwh)::numeric,
        3
    ) as q1_deviation_mwh,
    round(
        percentile_cont(0.75) within group (order by deviation_from_pn_mwh)::numeric,
        3
    ) as q3_deviation_mwh,
    round(sum(abs(metered_mwh)), 1) as abs_metered_mwh,
    round(sum(abs(pn_mwh)), 1) as abs_pn_mwh
from determinable
group by fuel_group
order by abs_metered_mwh desc
