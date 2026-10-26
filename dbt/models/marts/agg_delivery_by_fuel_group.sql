{{ config(materialized='table', group='analysis', tags=['results_mart']) }}

-- Delivery by fuel group: the single definition behind Q3 (metered against
-- notified) and Q12 (instructed against unexplained deviation). Determinable
-- periods with a PN, cohort units only, up to analysis_read_until; built by
-- hand when the README results are refreshed. The two analyses select from
-- this table. unit_periods_by_run is context only: how many unit-periods
-- each settlement run supplied, never a statistic per run.

{% set read_until = analysis_read_until() %}

with determinable as (
    select
        cohort.fuel_group,
        shortfall.settlement_date,
        shortfall.national_grid_bm_unit,
        coalesce(shortfall.metered_run_code, 'none') as metered_run_code,
        shortfall.instruction_status,
        shortfall.deviation_from_pn_mwh,
        shortfall.instructed_deviation_mwh,
        shortfall.unexplained_shortfall_mwh,
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
),

by_fuel_group as (
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
        ) as p25_deviation_mwh,
        round(
            percentile_cont(0.75) within group (order by deviation_from_pn_mwh)::numeric,
            3
        ) as p75_deviation_mwh,
        round(sum(abs(metered_mwh)), 1) as abs_metered_mwh,
        round(sum(abs(pn_mwh)), 1) as abs_pn_mwh,
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
        ) as instructed_share_of_abs_deviation
    from determinable
    group by fuel_group
),

periods_per_run as (
    select fuel_group, metered_run_code, count(*) as unit_periods
    from determinable
    group by fuel_group, metered_run_code
),

periods_by_run as (
    select
        fuel_group,
        jsonb_object_agg(metered_run_code, unit_periods order by metered_run_code)
            as unit_periods_by_run
    from periods_per_run
    group by fuel_group
)

select
    by_fuel_group.fuel_group,
    by_fuel_group.unit_periods,
    by_fuel_group.units,
    by_fuel_group.first_date,
    by_fuel_group.last_date,
    by_fuel_group.median_deviation_mwh,
    by_fuel_group.p25_deviation_mwh,
    by_fuel_group.p75_deviation_mwh,
    by_fuel_group.abs_metered_mwh,
    by_fuel_group.abs_pn_mwh,
    by_fuel_group.instructed_periods,
    by_fuel_group.instructed_period_share,
    by_fuel_group.abs_deviation_mwh,
    by_fuel_group.abs_instructed_deviation_mwh,
    by_fuel_group.abs_unexplained_shortfall_mwh,
    by_fuel_group.instructed_share_of_abs_deviation,
    periods_by_run.unit_periods_by_run,
{% if read_until is not none %}
    timestamptz '{{ read_until }}' as analysis_read_until
{% else %}
    null::timestamptz as analysis_read_until
{% endif %}
from by_fuel_group
inner join periods_by_run on by_fuel_group.fuel_group = periods_by_run.fuel_group
