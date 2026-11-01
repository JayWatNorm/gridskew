{{ config(group='analysis') }}

-- Q2-RF: how much does metered output change from R3 to RF? Registered
-- 12 October 2026, before any RF value was compared; amended 31 October
-- 2026, before any comparison. Population: the first 30 settlement dates
-- whose RF is stored (RF captured for settlement dates from 22 August 2025)
-- that also hold R3. Measures: the gross unit-level change
-- sum(abs(RF - R3)) / sum(abs(R3)) over unit-periods holding both, by fuel
-- group and overall, with the net change beside it; n, dates and coverage.
-- Headline: the change over cohort units, first; every BM unit and the
-- units outside the cohort beside it. If sum(abs(R3)) is zero for a group
-- or overall, the numerator, denominator and n are reported and that ratio
-- is marked not defined, never zero. Elexon's netted figures for suppliers
-- are not comparable: they cover other units and net their changes. Read
-- once, when 30 dates are stored. "Stored" means stored at or before
-- rf_restatement_capture_cutoff, the instant passed at the read, so a rerun
-- with the same cutoff reads the same rows. Settlement dates are taken in
-- date order, so an RF capture that arrives late, before the cutoff, takes
-- its place in the population. Until 30 dates are stored the query returns
-- counts and dates, no energy.
--
-- How the query reads it. A date holds a run when at least one row of that
-- run was stored by the cutoff (a null cutoff reads up to the query's own
-- time). R3 and RF are full-joined by unit, date and period: unit-periods in
-- only one run are coverage, not change. The fuel group comes from the
-- latest registry extract by Elexon ID. The cohort total counts each cohort
-- unit-period once; B1610 units outside the cohort (no published fuel type,
-- or not in the registry) form one group, and "overall" covers every unit.
-- Section 4 lists settlement dates with each run's periods against the
-- London calendar and when the RF capture first asks for the date; section
-- 5 gives the cutoff, when the population was reached or is projected to
-- be, and the registry extract's time. The earlier run is named once, here.

{% set earlier_run = 'R3' %}
{% set earlier = earlier_run | lower %}
{% set dates_needed = var('rf_restatement_dates_needed') %}
{% set capture_offset_days = var('b1610_rf_capture_offset_days') %}
{% set capture_cutoff = utc_instant_var('rf_restatement_capture_cutoff') %}
{% set cutoff_passed %}{% if capture_cutoff is none %}null::timestamptz{% else %}timestamptz '{{ capture_cutoff }}'{% endif %}{% endset %}
{% set stored_until %}{% if capture_cutoff is none %}now(){% else %}timestamptz '{{ capture_cutoff }}'{% endif %}{% endset %}

with date_runs as (
    select
        settlement_date,
        settlement_run_type,
        count(distinct settlement_period) as periods,
        min(retrieved_at) as first_stored_at
    from {{ source('elexon', 'elexon_b1610') }}
    where settlement_run_type in ('{{ earlier_run }}', 'RF')
        and retrieved_at <= {{ stored_until }}
    group by settlement_date, settlement_run_type
),

held_dates as (
    select
        settlement_date,
        coalesce(
            max(periods) filter (where settlement_run_type = '{{ earlier_run }}'), 0
        ) as earlier_periods,
        coalesce(max(periods) filter (where settlement_run_type = 'RF'), 0) as rf_periods,
        min(first_stored_at) filter (where settlement_run_type = 'RF') as rf_first_stored_at
    from date_runs
    group by settlement_date
),

dates_with_both_runs as (
    select
        settlement_date,
        row_number() over (order by settlement_date) as date_order
    from held_dates
    where earlier_periods > 0
        and rf_periods > 0
),

population_dates as (
    select settlement_date
    from dates_with_both_runs
    where date_order <= {{ dates_needed }}
),

gate as (
    select
        count(*) as dates,
        min(settlement_date) as first_date,
        max(settlement_date) as last_date,
        count(*) >= {{ dates_needed }} as is_reached
    from population_dates
),

-- A retry adds later rows to a date already held, so the unit-period rows
-- need the cutoff too. The date range lets the block-range index on
-- settlement_date serve the read; the join keeps out a date inside it that
-- holds only one run.
population_runs as (
    select
        b1610.bm_unit,
        b1610.settlement_date,
        b1610.settlement_period,
        b1610.settlement_run_type,
        b1610.quantity
    from {{ source('elexon', 'elexon_b1610') }} as b1610
    inner join population_dates
        on b1610.settlement_date = population_dates.settlement_date
    cross join gate
    where b1610.settlement_run_type in ('{{ earlier_run }}', 'RF')
        and b1610.retrieved_at <= {{ stored_until }}
        and b1610.settlement_date between gate.first_date and gate.last_date
),

earlier_run_rows as (
    select bm_unit, settlement_date, settlement_period, quantity
    from population_runs
    where settlement_run_type = '{{ earlier_run }}'
),

rf_rows as (
    select bm_unit, settlement_date, settlement_period, quantity
    from population_runs
    where settlement_run_type = 'RF'
),

unit_periods as (
    select
        coalesce(earlier_run_rows.bm_unit, rf_rows.bm_unit) as bm_unit,
        earlier_run_rows.quantity as earlier_mwh,
        rf_rows.quantity as rf_mwh
    from earlier_run_rows
    full join rf_rows
        on earlier_run_rows.bm_unit = rf_rows.bm_unit
        and earlier_run_rows.settlement_date = rf_rows.settlement_date
        and earlier_run_rows.settlement_period = rf_rows.settlement_period
),

cohort_fuel_groups as (
    select distinct
        elexon_bm_unit,
        fuel_group
    from {{ ref('int_elexon__bm_unit_cohort') }}
    where elexon_bm_unit is not null
),

grouped as (
    select
        unit_periods.bm_unit,
        unit_periods.earlier_mwh,
        unit_periods.rf_mwh,
        coalesce(cohort_fuel_groups.fuel_group, 'outside the cohort') as fuel_group,
        case when cohort_fuel_groups.elexon_bm_unit is null then 3 else 4 end as position
    from unit_periods
    left join cohort_fuel_groups
        on unit_periods.bm_unit = cohort_fuel_groups.elexon_bm_unit
),

{% set measure_columns %}
        count(*) filter (where earlier_mwh is not null and rf_mwh is not null) as unit_periods,
        count(distinct bm_unit) filter (where earlier_mwh is not null and rf_mwh is not null) as units,
        count(*) filter (where rf_mwh <> earlier_mwh) as changed_unit_periods,
        coalesce(sum(abs(rf_mwh - earlier_mwh)), 0) as gross_change_mwh,
        coalesce(sum(abs(earlier_mwh)) filter (where rf_mwh is not null), 0) as earlier_absolute_mwh,
        coalesce(sum(rf_mwh - earlier_mwh), 0) as net_change_mwh,
        count(*) filter (where rf_mwh is null) as only_in_earlier,
        count(*) filter (where earlier_mwh is null) as only_in_rf
{% endset %}

-- The cohort total and overall come straight from the unit-periods, not from
-- the fuel groups, so an Elexon ID in two fuel groups counts once in each.
-- With no GROUP BY each has a row even when no unit-period is stored.
measures as (
    select
        1 as position,
        'cohort total' as item,
        {{ measure_columns }}
    from unit_periods
    where bm_unit in (select elexon_bm_unit from cohort_fuel_groups)

    union all

    select
        2 as position,
        'overall' as item,
        {{ measure_columns }}
    from unit_periods

    union all

    select
        position,
        fuel_group as item,
        {{ measure_columns }}
    from grouped
    group by position, fuel_group
),

measure_rows as (
    select
        1 as section,
        measures.position,
        measures.item,
        measures.unit_periods,
        measures.units,
        case when gate.is_reached then measures.changed_unit_periods end as changed_unit_periods,
        case when gate.is_reached then round(measures.gross_change_mwh, 3) end as gross_change_mwh,
        case when gate.is_reached then round(measures.earlier_absolute_mwh, 3) end as earlier_absolute_mwh,
        case when gate.is_reached and measures.earlier_absolute_mwh > 0
            then round(measures.gross_change_mwh / measures.earlier_absolute_mwh, 6)
        end as gross_share,
        case when gate.is_reached then round(measures.net_change_mwh, 3) end as net_change_mwh,
        case when gate.is_reached and measures.earlier_absolute_mwh > 0
            then round(measures.net_change_mwh / measures.earlier_absolute_mwh, 6)
        end as net_share,
        case
            when not gate.is_reached then 'population not reached: no result'
            when measures.earlier_absolute_mwh = 0
                then 'not defined: sum of abs({{ earlier_run }}) is 0'
            else 'defined'
        end as share_status,
        measures.only_in_earlier,
        measures.only_in_rf,
        gate.dates,
        gate.first_date,
        gate.last_date
    from measures
    cross join gate
),

date_coverage as (
    select
        2 as section,
        'date ' || settlement_date || ': ' || settlement_run_type as item,
        count(*) as unit_periods,
        count(distinct bm_unit) as units,
        count(distinct settlement_period) as periods
    from population_runs
    group by settlement_date, settlement_run_type
),

repeated_cohort_ids as (
    select count(*) as repeated_ids
    from (
        select elexon_bm_unit
        from cohort_fuel_groups
        group by elexon_bm_unit
        having count(*) > 1
    ) as repeated
),

-- The RF capture's daily run starts when its one-day data interval ends, so
-- it first asks for a date at 00:00 UTC on the settlement date plus the
-- offset plus one day.
settlement_dates as (
    select
        held_dates.settlement_date,
        held_dates.earlier_periods,
        held_dates.rf_periods,
        held_dates.rf_first_stored_at,
        {{ settlement_period_count('held_dates.settlement_date') }} as expected_periods,
        (held_dates.settlement_date + {{ capture_offset_days }} + 1)::timestamp
            at time zone 'UTC' as rf_capture_due_at,
        population_dates.settlement_date is not null as is_in_population
    from held_dates
    left join population_dates
        on held_dates.settlement_date = population_dates.settlement_date
),

-- A date's RF is expected when it was first stored; one not yet held, at the
-- later of its due time and the cutoff (a missing date is taken to arrive
-- now, a future one when due).
settlement_date_arrivals as (
    select
        settlement_date,
        earlier_periods,
        rf_periods,
        rf_first_stored_at,
        expected_periods,
        rf_capture_due_at,
        is_in_population,
        case
            when rf_periods > 0 then rf_first_stored_at
            else greatest(rf_capture_due_at, {{ stored_until }})
        end as expected_rf_at
    from settlement_dates
),

-- The population is reached when the Nth date holding the earlier run has
-- its RF stored: with N held, the moment that happened; otherwise a
-- projection.
reach as (
    select
        count(*) >= {{ dates_needed }} as is_projected,
        max(expected_rf_at) filter (where arrival_order = {{ dates_needed }}) as reached_at
    from (
        select
            expected_rf_at,
            row_number() over (order by expected_rf_at, settlement_date) as arrival_order
        from settlement_date_arrivals
        where earlier_periods > 0
    ) as earlier_run_dates
),

settlement_date_rows as (
    select
        4 as section,
        'date ' || settlement_date_arrivals.settlement_date as item,
        settlement_date_arrivals.settlement_date,
        case
            when settlement_date_arrivals.is_in_population then 'in the population'
            when settlement_date_arrivals.earlier_periods > 0
                and settlement_date_arrivals.rf_periods > 0
                then '{{ earlier_run }} and RF held, after the population'
            when settlement_date_arrivals.rf_periods > 0
                then 'RF held, no {{ earlier_run }}'
            when settlement_date_arrivals.rf_capture_due_at <= {{ stored_until }}
                then 'RF due, not held'
            else 'RF not yet due'
        end as date_status,
        settlement_date_arrivals.expected_periods,
        settlement_date_arrivals.earlier_periods,
        settlement_date_arrivals.rf_periods,
        settlement_date_arrivals.rf_capture_due_at,
        settlement_date_arrivals.rf_first_stored_at
    from settlement_date_arrivals
    cross join reach
    where settlement_date_arrivals.rf_periods > 0
        or (
            settlement_date_arrivals.earlier_periods > 0
            and settlement_date_arrivals.rf_capture_due_at <= {{ stored_until }}
        )
        or (
            settlement_date_arrivals.earlier_periods > 0
            and settlement_date_arrivals.expected_rf_at <= reach.reached_at
        )
),

read_rows as (
    select
        5 as section,
        1 as position,
        'capture cutoff' as item,
        {{ cutoff_passed }} as instant

    union all

    select
        5,
        2,
        case
            when gate.is_reached then 'population reached at'
            when reach.is_projected then 'population projected at'
            else 'population not projected: fewer dates hold {{ earlier_run }} than needed'
        end,
        reach.reached_at
    from gate
    cross join reach

    union all

    select
        5,
        3,
        'registry extract retrieved at',
        max(retrieved_at)
    from {{ ref('int_elexon__bm_units_current') }}
)

select
    section,
    item,
    unit_periods,
    units,
    changed_unit_periods,
    gross_change_mwh,
    earlier_absolute_mwh as {{ earlier }}_absolute_mwh,
    gross_share,
    net_change_mwh,
    net_share,
    share_status,
    only_in_earlier as only_in_{{ earlier }},
    only_in_rf,
    null::bigint as periods,
    dates,
    first_date,
    last_date,
    position,
    null::date as settlement_date,
    null::text as date_status,
    null::integer as expected_periods,
    null::bigint as {{ earlier }}_periods,
    null::bigint as rf_periods,
    null::timestamptz as rf_capture_due_at,
    null::timestamptz as rf_first_stored_at,
    null::timestamptz as instant
from measure_rows

union all

select
    section,
    item,
    unit_periods,
    units,
    null,
    null,
    null,
    null,
    null,
    null,
    null,
    null,
    null,
    periods,
    null,
    null,
    null,
    null,
    null,
    null,
    null,
    null,
    null,
    null,
    null,
    null
from date_coverage

union all

select
    3,
    'Elexon IDs with more than one cohort fuel group (each would count twice)',
    null,
    repeated_ids,
    null,
    null,
    null,
    null,
    null,
    null,
    null,
    null,
    null,
    null,
    null,
    null,
    null,
    null,
    null,
    null,
    null,
    null,
    null,
    null,
    null,
    null
from repeated_cohort_ids

union all

select
    section,
    item,
    null,
    null,
    null,
    null,
    null,
    null,
    null,
    null,
    null,
    null,
    null,
    null,
    null,
    null,
    null,
    null,
    settlement_date,
    date_status,
    expected_periods,
    earlier_periods,
    rf_periods,
    rf_capture_due_at,
    rf_first_stored_at,
    null
from settlement_date_rows

union all

select
    section,
    item,
    null,
    null,
    null,
    null,
    null,
    null,
    null,
    null,
    null,
    null,
    null,
    null,
    null,
    null,
    null,
    position,
    null,
    null,
    null,
    null,
    null,
    null,
    null,
    instant
from read_rows

order by section, position, settlement_date, item
