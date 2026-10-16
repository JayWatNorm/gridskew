{{ config(group='analysis') }}

-- Q25: how fragmented are instructions to storage compared with thermal
-- plant? Reading rule: research questions, Q25 (amended 12 October 2026,
-- decision 024). Per fuel group and calendar month, each with its n:
--
-- 1. Dispatch granularity: the share of acceptances whose own span (first
--    ramp point's start to last ramp point's end, latest capture) is under
--    10 minutes, and the median span. Not called CADL.
-- 2. Continuous acceptance duration as BSC Section T Annex T-1 Part 2
--    paragraph 12 defines it, per acceptance k: the related acceptances are
--    the unit's acceptances issued from the start of the settlement period
--    three before the one holding k's acceptance time to the end of the
--    period three after it (12.1); continuity, overlapping or touching,
--    chains through related acceptances only (12.2); the duration runs from
--    the earliest to the latest time of k and its continuous acceptances
--    (12.3); the share under 10 minutes is the share CADL would flag (12.4,
--    CADL 10 minutes, 1.9.1). Counted in acceptances. Read only after its
--    check against Elexon's cadlFlag.
-- 3. For comparison, the simpler overlap chain: every acceptance of a unit
--    chained while each starts no later than the latest end so far, with no
--    12.1 window; counted in chains. A BSC chain is always part of one of
--    these, which is why measure 2 searches for related acceptances inside
--    them.
--
-- Month is the calendar month of the first ramp point's settlement date.
-- Section 3 is coverage. Run once, by hand, after the rule is recorded.

{% set read_until = var('analysis_read_until') %}

with ramp_points as (
    select
        national_grid_bm_unit,
        acceptance_number,
        acceptance_time,
        settlement_date,
        time_from,
        time_to,
        retrieved_at,
        max(retrieved_at) over (
            partition by national_grid_bm_unit, acceptance_number
        ) as latest_retrieved_at
    from {{ ref('stg_elexon__boalf') }}
),

acceptances as (
    select
        national_grid_bm_unit,
        acceptance_number,
        min(acceptance_time) as acceptance_time,
        min(settlement_date) as first_settlement_date,
        min(time_from) as span_from,
        max(time_to) as span_to
    from ramp_points
    where retrieved_at = latest_retrieved_at
    group by national_grid_bm_unit, acceptance_number
),

cohort_acceptances as (
    select
        acceptances.national_grid_bm_unit,
        acceptances.acceptance_number,
        acceptances.acceptance_time,
        date_trunc('month', acceptances.first_settlement_date)::date as settlement_month,
        acceptances.span_from,
        acceptances.span_to,
        cohort.fuel_group
    from acceptances
    inner join {{ ref('int_elexon__bm_unit_cohort') }} as cohort
        on acceptances.national_grid_bm_unit = cohort.national_grid_bm_unit
{% if read_until is not none %}
    where acceptances.span_from < timestamptz '{{ read_until }}' + interval '30 minutes'
{% endif %}
),

ordered as (
    select
        national_grid_bm_unit,
        acceptance_number,
        acceptance_time,
        settlement_month,
        span_from,
        span_to,
        fuel_group,
        max(span_to) over (
            partition by national_grid_bm_unit
            order by span_from, acceptance_number
            rows between unbounded preceding and 1 preceding
        ) as latest_end_before
    from cohort_acceptances
),

chain_starts as (
    select
        national_grid_bm_unit,
        acceptance_number,
        acceptance_time,
        settlement_month,
        span_from,
        span_to,
        fuel_group,
        case
            when latest_end_before is null or span_from > latest_end_before then 1
            else 0
        end as starts_chain
    from ordered
),

chain_members as (
    select
        national_grid_bm_unit,
        acceptance_number,
        acceptance_time,
        settlement_month,
        span_from,
        span_to,
        fuel_group,
        sum(starts_chain) over (
            partition by national_grid_bm_unit
            order by span_from, acceptance_number
        ) as chain_number
    from chain_starts
),

chains as (
    select
        national_grid_bm_unit,
        chain_number,
        fuel_group,
        min(settlement_month) as settlement_month,
        count(*) as acceptances,
        max(span_to) - min(span_from) as duration
    from chain_members
    group by national_grid_bm_unit, chain_number, fuel_group
),

-- 12.1: settlement periods start on UTC half-hours, so the period an
-- acceptance was issued in is its acceptance time binned to 30 minutes, and
-- k's related acceptances are those issued from the start of the period
-- three before k's to and including the end of the period three after it.
-- Joining on the period (one row of k per period offset) finds exactly
-- those, without comparing every pair in a long chain.
issued_periods as (
    select
        national_grid_bm_unit,
        acceptance_number,
        chain_number,
        acceptance_time,
        span_from,
        span_to,
        date_bin(interval '30 minutes', acceptance_time, timestamptz '2000-01-01 00:00:00+00')
            as issued_period
    from chain_members
),

-- An acceptance issued exactly on a half-hour boundary is also at the end
-- spot time of the period before, which 12.1 includes.
member_periods as (
    select
        national_grid_bm_unit,
        acceptance_number,
        chain_number,
        span_from,
        span_to,
        issued_period as member_period
    from issued_periods

    union all

    select
        national_grid_bm_unit,
        acceptance_number,
        chain_number,
        span_from,
        span_to,
        issued_period - interval '30 minutes'
    from issued_periods
    where acceptance_time = issued_period
),

-- Materialised so the planner joins on the period rather than on the chain
-- alone (which compares every pair in a long chain).
related_periods as materialized (
    select
        issued_periods.national_grid_bm_unit,
        issued_periods.acceptance_number,
        issued_periods.chain_number,
        issued_periods.issued_period + period_offset * interval '30 minutes' as related_period
    from issued_periods
    cross join generate_series(-3, 3) as period_offset
),

related as (
    select distinct
        windows.national_grid_bm_unit,
        windows.acceptance_number as k_number,
        members.acceptance_number as related_number,
        members.span_from,
        members.span_to
    from related_periods as windows
    inner join member_periods as members
        on windows.national_grid_bm_unit = members.national_grid_bm_unit
        and windows.chain_number = members.chain_number
        and windows.related_period = members.member_period
),

related_ordered as (
    select
        national_grid_bm_unit,
        k_number,
        related_number,
        span_from,
        span_to,
        max(span_to) over (
            partition by national_grid_bm_unit, k_number
            order by span_from, related_number
            rows between unbounded preceding and 1 preceding
        ) as latest_end_before
    from related
),

related_islands as (
    select
        national_grid_bm_unit,
        k_number,
        related_number,
        span_from,
        span_to,
        sum(
            case
                when latest_end_before is null or span_from > latest_end_before then 1
                else 0
            end
        ) over (
            partition by national_grid_bm_unit, k_number
            order by span_from, related_number
        ) as island
    from related_ordered
),

-- 12.2 and 12.3: the island of related acceptances that holds k itself.
continuous_durations as (
    select
        members.national_grid_bm_unit,
        members.k_number,
        max(members.span_to) - min(members.span_from) as duration
    from related_islands as members
    inner join related_islands as own
        on members.national_grid_bm_unit = own.national_grid_bm_unit
        and members.k_number = own.k_number
        and own.related_number = own.k_number
        and members.island = own.island
    group by members.national_grid_bm_unit, members.k_number
),

granularity as (
    select
        fuel_group,
        settlement_month,
        span_to - span_from as duration
    from cohort_acceptances
),

continuous as (
    select
        chain_members.fuel_group,
        chain_members.settlement_month,
        continuous_durations.duration
    from continuous_durations
    inner join chain_members
        on continuous_durations.national_grid_bm_unit = chain_members.national_grid_bm_unit
        and continuous_durations.k_number = chain_members.acceptance_number
),

measures as (
    select
        '1 dispatch granularity (acceptances)' as measure,
        fuel_group,
        settlement_month,
        duration
    from granularity

    union all

    select
        '2 continuous acceptance duration, BSC (acceptances)',
        fuel_group,
        settlement_month,
        duration
    from continuous

    union all

    select
        '3 overlap chains, no 12.1 window (chains)',
        fuel_group,
        settlement_month,
        duration
    from chains
),

by_month as (
    select
        1 as section,
        fuel_group,
        settlement_month,
        measure,
        count(*) as n,
        count(*) filter (where duration < interval '10 minutes') as under_10_minutes,
        round(100.0 * count(*) filter (where duration < interval '10 minutes') / count(*), 1)
            as under_10_minutes_pct,
        round((percentile_cont(0.5) within group (
            order by extract(epoch from duration) / 60
        ))::numeric, 1) as median_minutes
    from measures
    group by fuel_group, settlement_month, measure
),

all_months as (
    select
        2 as section,
        fuel_group,
        null::date as settlement_month,
        measure,
        count(*) as n,
        count(*) filter (where duration < interval '10 minutes') as under_10_minutes,
        round(100.0 * count(*) filter (where duration < interval '10 minutes') / count(*), 1)
            as under_10_minutes_pct,
        round((percentile_cont(0.5) within group (
            order by extract(epoch from duration) / 60
        ))::numeric, 1) as median_minutes
    from measures
    group by fuel_group, measure
),

captures_per_acceptance as (
    select
        national_grid_bm_unit,
        acceptance_number,
        count(distinct retrieved_at) as captures
    from ramp_points
    group by national_grid_bm_unit, acceptance_number
),

coverage as (
    select
        3 as section,
        null::text as fuel_group,
        null::date as settlement_month,
        'acceptances of units outside the cohort' as measure,
        count(*) as n,
        null::bigint as under_10_minutes,
        null::numeric as under_10_minutes_pct,
        null::numeric as median_minutes
    from acceptances
    left join {{ ref('int_elexon__bm_unit_cohort') }} as cohort
        on acceptances.national_grid_bm_unit = cohort.national_grid_bm_unit
    where cohort.national_grid_bm_unit is null
{% if read_until is not none %}
        and acceptances.span_from < timestamptz '{{ read_until }}' + interval '30 minutes'
{% endif %}

    union all

    select
        3,
        null,
        null,
        'acceptances captured more than once (latest capture used)',
        count(*),
        null,
        null,
        null
    from captures_per_acceptance
    where captures > 1
)

select
    section,
    fuel_group,
    settlement_month,
    measure,
    n,
    under_10_minutes,
    under_10_minutes_pct,
    median_minutes
from by_month
union all
select
    section,
    fuel_group,
    settlement_month,
    measure,
    n,
    under_10_minutes,
    under_10_minutes_pct,
    median_minutes
from all_months
union all
select
    section,
    fuel_group,
    settlement_month,
    measure,
    n,
    under_10_minutes,
    under_10_minutes_pct,
    median_minutes
from coverage
order by section, fuel_group, settlement_month, measure
