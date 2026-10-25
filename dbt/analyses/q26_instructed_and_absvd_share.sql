{{ config(group='analysis') }}

-- Q26: how much of the deviation from PN do the system operator's balancing
-- actions explain once services settled outside the balancing mechanism
-- (ABSVD) are counted? Reading rule: research questions, Q26 (recorded 12
-- October 2026, before any ABSVD capture). Per fuel group, over
-- determinable cohort periods with a PN whose settlement date has been
-- captured (absvd_mwh not null):
-- (sum|instructed_deviation| + sum|absvd|) / (sum|instructed_deviation| +
-- sum|absvd| + sum|unexplained_after_absvd|). It is a share of the three
-- components' sizes, not of each period's own deviation (signed parts can
-- offset within a period), as Q12. Beside it: n, the three sums, the date
-- range, the share of periods with any ABSVD, the periods left out because
-- their settlement date was never captured, and Q12's share over the same
-- periods for comparison. A group with no included period, or whose three
-- sums are all zero, reports the share as null (N/A) with its n. ABSVD is the
-- latest value captured, not called final. Run once, by hand, up to
-- analysis_read_until.

{% set read_until = analysis_read_until() %}

with determinable as (
    select
        cohort.fuel_group,
        shortfall.settlement_date,
        shortfall.instructed_deviation_mwh,
        shortfall.unexplained_shortfall_mwh,
        shortfall.absvd_mwh,
        shortfall.unexplained_after_absvd_mwh
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

sums as (
    select
        fuel_group,
        count(*) filter (where absvd_mwh is not null) as unit_periods,
        count(*) filter (where absvd_mwh is null) as left_out_uncaptured_date,
        min(settlement_date) filter (where absvd_mwh is not null) as first_date,
        max(settlement_date) filter (where absvd_mwh is not null) as last_date,
        count(*) filter (where absvd_mwh <> 0) as periods_with_absvd,
        coalesce(sum(abs(instructed_deviation_mwh)) filter (where absvd_mwh is not null), 0)
            as abs_instructed,
        coalesce(sum(abs(absvd_mwh)) filter (where absvd_mwh is not null), 0)
            as abs_absvd,
        coalesce(sum(abs(unexplained_after_absvd_mwh)) filter (where absvd_mwh is not null), 0)
            as abs_unexplained_after,
        coalesce(sum(abs(unexplained_shortfall_mwh)) filter (where absvd_mwh is not null), 0)
            as abs_unexplained_before
    from determinable
    group by fuel_group
)

select
    fuel_group,
    unit_periods,
    first_date,
    last_date,
    periods_with_absvd,
    round(periods_with_absvd::numeric / nullif(unit_periods, 0), 6)
        as share_of_periods_with_absvd,
    left_out_uncaptured_date,
    round(abs_instructed, 1) as abs_instructed_deviation_mwh,
    round(abs_absvd, 1) as abs_absvd_mwh,
    round(abs_unexplained_after, 1) as abs_unexplained_after_absvd_mwh,
    round(
        (abs_instructed + abs_absvd)
        / nullif(abs_instructed + abs_absvd + abs_unexplained_after, 0),
        6
    ) as explained_share_with_absvd,
    round(abs_instructed / nullif(abs_instructed + abs_unexplained_before, 0), 6)
        as q12_instructed_share_same_periods
from sums
order by fuel_group
