{{ config(group='analysis') }}

-- Q24: what share of each fuel group's instructed energy is flagged as a
-- system action rather than energy balancing? Reading rule: research
-- questions, Q24 (amended 12 October 2026, decision 024; not called
-- pre-registered). "Flagged as system actions" is NESO's real-time SO flag,
-- not constraint volume.
--
-- Acceptance volume of a stretch of int_elexon__instruction_intervals (one
-- acceptance in force) is the integral of |instructed level - PN| over the
-- stretch. The instructed level at each end of the stretch comes from the
-- latest capture of the acceptance's ramp point that spans it (the stretch
-- lies on one ramp segment), the PN from the capture fct_commitments chose.
-- The stretch is cut at every PN breakpoint; on each piece both lines are
-- straight, so the integral is exact, split where they cross. A stretch is
-- left out and counted in section 3 when no single ramp segment spans it or
-- its period has no complete PN capture; section 3 also counts stretches
-- whose energy from the end levels differs from the stored instructed_mwh
-- (a check that the levels were placed right), and the stretches where the
-- two lines cross, with the volume the absolute net would have missed.
-- Section 1 is per fuel group and calendar month of settlement date: the
-- SO-flagged share of acceptance volume, and beside it the share of level
-- energy (the measure read on 11 October) over the same stretches. Section 2
-- is all months together. Run once, by hand, after the rule is recorded.

{% set read_until = analysis_read_until() %}

with stretches as (
    select
        intervals.national_grid_bm_unit,
        intervals.period_start_utc,
        intervals.interval_from,
        intervals.interval_to,
        intervals.acceptance_number,
        intervals.so_flag,
        intervals.instructed_mwh,
        cohort.fuel_group
    from {{ ref('int_elexon__instruction_intervals') }} as intervals
    inner join {{ ref('int_elexon__bm_unit_cohort') }} as cohort
        on intervals.national_grid_bm_unit = cohort.national_grid_bm_unit
    -- A placeholder row marks a half-hour with no stretch left.
    where intervals.interval_from is not null
{% if read_until is not none %}
        and intervals.period_start_utc <= timestamptz '{{ read_until }}'
{% endif %}
),

ramp_point_captures as (
    select
        national_grid_bm_unit,
        acceptance_number,
        time_from,
        time_to,
        level_from,
        level_to,
        retrieved_at,
        max(retrieved_at) over (
            partition by national_grid_bm_unit, acceptance_number, time_from
        ) as latest_retrieved_at
    from {{ ref('stg_elexon__boalf') }}
),

stretch_levels as (
    select
        stretches.national_grid_bm_unit,
        stretches.period_start_utc,
        stretches.interval_from,
        stretches.interval_to,
        {{ ramp_level_at(
            'ramp_points.level_from', 'ramp_points.level_to',
            'ramp_points.time_from', 'ramp_points.time_to',
            'stretches.interval_from'
        ) }} as instructed_mw_from,
        {{ ramp_level_at(
            'ramp_points.level_from', 'ramp_points.level_to',
            'ramp_points.time_from', 'ramp_points.time_to',
            'stretches.interval_to'
        ) }} as instructed_mw_to
    from stretches
    inner join ramp_point_captures as ramp_points
        on stretches.national_grid_bm_unit = ramp_points.national_grid_bm_unit
        and stretches.acceptance_number = ramp_points.acceptance_number
        and ramp_points.retrieved_at = ramp_points.latest_retrieved_at
        and ramp_points.time_to > ramp_points.time_from
        and ramp_points.time_from <= stretches.interval_from
        and ramp_points.time_to >= stretches.interval_to
),

stretch_commitments as (
    select
        stretch_levels.national_grid_bm_unit,
        stretch_levels.period_start_utc,
        stretch_levels.interval_from,
        stretch_levels.interval_to,
        stretch_levels.instructed_mw_from,
        stretch_levels.instructed_mw_to,
        commitments.settlement_date,
        commitments.settlement_period,
        commitments.pn_retrieved_at
    from stretch_levels
    inner join {{ ref('fct_commitments') }} as commitments
        on stretch_levels.national_grid_bm_unit = commitments.national_grid_bm_unit
        and stretch_levels.period_start_utc = commitments.period_start_utc
    where commitments.coverage_status = 'complete'
),

-- The stretch cut at the PN's breakpoints: on each piece both lines are
-- straight. Differences are instructed minus PN, in MW.
pieces as (
    select
        stretch_commitments.national_grid_bm_unit,
        stretch_commitments.period_start_utc,
        stretch_commitments.interval_from,
        stretch_commitments.settlement_date,
        extract(epoch from (
            least(segments.time_to, stretch_commitments.interval_to)
            - greatest(segments.time_from, stretch_commitments.interval_from)
        )) / 3600 as piece_hours,
        {{ ramp_level_at(
            'stretch_commitments.instructed_mw_from', 'stretch_commitments.instructed_mw_to',
            'stretch_commitments.interval_from', 'stretch_commitments.interval_to',
            'greatest(segments.time_from, stretch_commitments.interval_from)'
        ) }} as instructed_mw_start,
        {{ ramp_level_at(
            'stretch_commitments.instructed_mw_from', 'stretch_commitments.instructed_mw_to',
            'stretch_commitments.interval_from', 'stretch_commitments.interval_to',
            'least(segments.time_to, stretch_commitments.interval_to)'
        ) }} as instructed_mw_end,
        {{ ramp_level_at(
            'segments.level_from', 'segments.level_to',
            'segments.time_from', 'segments.time_to',
            'greatest(segments.time_from, stretch_commitments.interval_from)'
        ) }} as pn_mw_start,
        {{ ramp_level_at(
            'segments.level_from', 'segments.level_to',
            'segments.time_from', 'segments.time_to',
            'least(segments.time_to, stretch_commitments.interval_to)'
        ) }} as pn_mw_end
    from stretch_commitments
    inner join {{ ref('stg_elexon__pn') }} as segments
        on segments.national_grid_bm_unit = stretch_commitments.national_grid_bm_unit
        and segments.settlement_date = stretch_commitments.settlement_date
        and segments.settlement_period = stretch_commitments.settlement_period
        and segments.retrieved_at = stretch_commitments.pn_retrieved_at
        and segments.time_to > segments.time_from
        and segments.time_from < stretch_commitments.interval_to
        and segments.time_to > stretch_commitments.interval_from
),

piece_differences as (
    select
        national_grid_bm_unit,
        period_start_utc,
        interval_from,
        settlement_date,
        piece_hours,
        instructed_mw_start,
        instructed_mw_end,
        instructed_mw_start - pn_mw_start as difference_mw_start,
        instructed_mw_end - pn_mw_end as difference_mw_end
    from pieces
),

stretch_volumes as (
    select
        national_grid_bm_unit,
        period_start_utc,
        interval_from,
        settlement_date,
        sum(
            case
                when difference_mw_start * difference_mw_end >= 0
                    then piece_hours * (abs(difference_mw_start) + abs(difference_mw_end)) / 2
                -- The lines cross inside the piece: two triangles.
                else piece_hours
                    * (difference_mw_start * difference_mw_start + difference_mw_end * difference_mw_end)
                    / (2 * (abs(difference_mw_start) + abs(difference_mw_end)))
            end
        ) as acceptance_volume_mwh,
        abs(sum(piece_hours * (difference_mw_start + difference_mw_end) / 2)) as absolute_net_mwh,
        sum(piece_hours * (instructed_mw_start + instructed_mw_end) / 2) as instructed_mwh_from_levels,
        bool_or(difference_mw_start * difference_mw_end < 0) as lines_cross
    from piece_differences
    group by national_grid_bm_unit, period_start_utc, interval_from, settlement_date
),

volumes as (
    select
        stretches.national_grid_bm_unit,
        stretches.acceptance_number,
        stretches.fuel_group,
        stretches.so_flag,
        date_trunc('month', stretch_volumes.settlement_date)::date as settlement_month,
        stretch_volumes.lines_cross,
        stretch_volumes.absolute_net_mwh,
        abs(stretch_volumes.instructed_mwh_from_levels - stretches.instructed_mwh) > 0.001
            as levels_disagree_with_stored_energy,
        abs(stretches.instructed_mwh) as level_energy_mwh,
        stretch_volumes.acceptance_volume_mwh
    from stretches
    inner join stretch_volumes
        on stretches.national_grid_bm_unit = stretch_volumes.national_grid_bm_unit
        and stretches.period_start_utc = stretch_volumes.period_start_utc
        and stretches.interval_from = stretch_volumes.interval_from
),

left_out as (
    select
        stretches.fuel_group,
        stretches.instructed_mwh,
        case
            when stretch_levels.national_grid_bm_unit is null
                then 'left out: no single ramp segment spans the stretch'
            else 'left out: no complete PN capture'
        end as reason
    from stretches
    left join stretch_levels
        on stretches.national_grid_bm_unit = stretch_levels.national_grid_bm_unit
        and stretches.period_start_utc = stretch_levels.period_start_utc
        and stretches.interval_from = stretch_levels.interval_from
    left join stretch_volumes
        on stretches.national_grid_bm_unit = stretch_volumes.national_grid_bm_unit
        and stretches.period_start_utc = stretch_volumes.period_start_utc
        and stretches.interval_from = stretch_volumes.interval_from
    where stretch_volumes.national_grid_bm_unit is null
),

by_month as (
    select
        1 as section,
        fuel_group,
        settlement_month,
        'stretches with a volume' as item,
        count(distinct (national_grid_bm_unit, acceptance_number)) as acceptances,
        count(*) as stretches,
        round(sum(acceptance_volume_mwh), 1) as acceptance_volume_mwh,
        round(coalesce(sum(acceptance_volume_mwh) filter (where so_flag), 0), 1) as so_flagged_volume_mwh,
        round(100 * coalesce(sum(acceptance_volume_mwh) filter (where so_flag), 0)
            / nullif(sum(acceptance_volume_mwh), 0), 1) as so_flagged_volume_pct,
        round(sum(level_energy_mwh), 1) as level_energy_mwh,
        round(100 * coalesce(sum(level_energy_mwh) filter (where so_flag), 0)
            / nullif(sum(level_energy_mwh), 0), 1) as so_flagged_level_energy_pct
    from volumes
    group by fuel_group, settlement_month
),

all_months as (
    select
        2 as section,
        fuel_group,
        null::date as settlement_month,
        'stretches with a volume, all months' as item,
        count(distinct (national_grid_bm_unit, acceptance_number)) as acceptances,
        count(*) as stretches,
        round(sum(acceptance_volume_mwh), 1) as acceptance_volume_mwh,
        round(coalesce(sum(acceptance_volume_mwh) filter (where so_flag), 0), 1) as so_flagged_volume_mwh,
        round(100 * coalesce(sum(acceptance_volume_mwh) filter (where so_flag), 0)
            / nullif(sum(acceptance_volume_mwh), 0), 1) as so_flagged_volume_pct,
        round(sum(level_energy_mwh), 1) as level_energy_mwh,
        round(100 * coalesce(sum(level_energy_mwh) filter (where so_flag), 0)
            / nullif(sum(level_energy_mwh), 0), 1) as so_flagged_level_energy_pct
    from volumes
    group by fuel_group
),

coverage as (
    select
        3 as section,
        fuel_group,
        null::date as settlement_month,
        reason as item,
        null::bigint as acceptances,
        count(*) as stretches,
        null::numeric as acceptance_volume_mwh,
        null::numeric as so_flagged_volume_mwh,
        null::numeric as so_flagged_volume_pct,
        round(sum(abs(instructed_mwh)), 1) as level_energy_mwh,
        null::numeric as so_flagged_level_energy_pct
    from left_out
    group by fuel_group, reason

    union all

    -- Volume is the full integral; so_flagged_volume_mwh here holds what the
    -- absolute net would have reported for the same stretches.
    select
        3,
        fuel_group,
        null,
        'stretches where instructed and PN cross (volume; absolute net beside it)',
        null,
        count(*),
        round(sum(acceptance_volume_mwh), 1),
        round(sum(absolute_net_mwh), 1),
        null,
        null,
        null
    from volumes
    where lines_cross
    group by fuel_group

    union all

    select
        3,
        fuel_group,
        null,
        'stretches whose end levels do not reproduce the stored instructed_mwh',
        null,
        count(*),
        null,
        null,
        null,
        round(sum(level_energy_mwh), 1),
        null
    from volumes
    where levels_disagree_with_stored_energy
    group by fuel_group
)

select
    section,
    fuel_group,
    settlement_month,
    item,
    acceptances,
    stretches,
    acceptance_volume_mwh,
    so_flagged_volume_mwh,
    so_flagged_volume_pct,
    level_energy_mwh,
    so_flagged_level_energy_pct
from by_month
union all
select
    section,
    fuel_group,
    settlement_month,
    item,
    acceptances,
    stretches,
    acceptance_volume_mwh,
    so_flagged_volume_mwh,
    so_flagged_volume_pct,
    level_energy_mwh,
    so_flagged_level_energy_pct
from all_months
union all
select
    section,
    fuel_group,
    settlement_month,
    item,
    acceptances,
    stretches,
    acceptance_volume_mwh,
    so_flagged_volume_mwh,
    so_flagged_volume_pct,
    level_energy_mwh,
    so_flagged_level_energy_pct
from coverage
order by section, fuel_group, settlement_month, item
