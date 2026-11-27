{{ config(group='analysis') }}

-- Q37: when NESO turns wind farms down, what does it turn up in the same
-- half-hours? Reading rule: research questions, Q37.
--
-- The measure is Q24's acceptance volume with its direction kept. A stretch
-- of int_elexon__instruction_intervals (one acceptance in force) is cut at
-- every PN breakpoint; on each piece the instructed level and the PN are
-- straight lines, so the energy between them is exact. It is turned down
-- where the instructed level is below the PN and turned up where it is
-- above; a piece in which the lines cross gives a part to each. A stretch
-- is left out and counted in section 4 when no single ramp segment spans it
-- or its period has no complete PN capture.
--
-- Units: every unit with a stretch and a complete PN capture. A unit outside
-- the cohort (no published fuel type) is in the group
-- 'no published fuel type'.
--
-- Half-hours: every half-hour in which at least one wind unit has a complete
-- PN capture, from the first half-hour that holds a stretch to the cutoff.
-- Energy in any other half-hour is counted in section 4.
--
-- Section 1 is per calendar month of settlement date, section 2 all months
-- together: the wind units' flagged and total turned-down energy, their
-- planned (PN) energy, and the half-hours with any flagged wind turn-down,
-- with 50 MWh or more (100 MW over the half-hour) and with 500 MWh or more
-- (1 GW). Section 3 places each half-hour in a band by its flagged wind
-- turn-down and gives, per band and fuel group, the energy turned up and
-- turned down, each with its flagged part. Run once, by hand, after the
-- rule is recorded.

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
        coalesce(cohort.fuel_group, 'no published fuel type') as fuel_group
    from {{ ref('int_elexon__instruction_intervals') }} as intervals
    left join {{ ref('int_elexon__bm_unit_cohort') }} as cohort
        on intervals.national_grid_bm_unit = cohort.national_grid_bm_unit
    -- A placeholder row marks a half-hour with no stretch left.
    where intervals.interval_from is not null
{% if read_until is not none %}
        and intervals.period_start_utc <= timestamptz '{{ read_until }}'
{% endif %}
),

first_stretch as (
    select min(period_start_utc) as period_start_utc
    from stretches
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
        extract(epoch from (
            least(segments.time_to, stretch_commitments.interval_to)
            - greatest(segments.time_from, stretch_commitments.interval_from)
        )) / 3600 as piece_hours,
        {{ ramp_level_at(
            'stretch_commitments.instructed_mw_from', 'stretch_commitments.instructed_mw_to',
            'stretch_commitments.interval_from', 'stretch_commitments.interval_to',
            'greatest(segments.time_from, stretch_commitments.interval_from)'
        ) }}
        - {{ ramp_level_at(
            'segments.level_from', 'segments.level_to',
            'segments.time_from', 'segments.time_to',
            'greatest(segments.time_from, stretch_commitments.interval_from)'
        ) }} as difference_mw_start,
        {{ ramp_level_at(
            'stretch_commitments.instructed_mw_from', 'stretch_commitments.instructed_mw_to',
            'stretch_commitments.interval_from', 'stretch_commitments.interval_to',
            'least(segments.time_to, stretch_commitments.interval_to)'
        ) }}
        - {{ ramp_level_at(
            'segments.level_from', 'segments.level_to',
            'segments.time_from', 'segments.time_to',
            'least(segments.time_to, stretch_commitments.interval_to)'
        ) }} as difference_mw_end
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

-- Where the lines cross inside a piece, each side is a triangle: its height
-- is the difference at its end and its share of the piece's time is that
-- height over the two heights together.
stretch_energy as (
    select
        national_grid_bm_unit,
        period_start_utc,
        interval_from,
        sum(
            case
                when difference_mw_start * difference_mw_end >= 0
                    then piece_hours
                        * (greatest(difference_mw_start, 0) + greatest(difference_mw_end, 0))
                        / 2
                else piece_hours
                    * greatest(difference_mw_start, difference_mw_end)
                    * greatest(difference_mw_start, difference_mw_end)
                    / (2 * (abs(difference_mw_start) + abs(difference_mw_end)))
            end
        ) as turned_up_mwh,
        sum(
            case
                when difference_mw_start * difference_mw_end >= 0
                    then piece_hours
                        * (greatest(-difference_mw_start, 0) + greatest(-difference_mw_end, 0))
                        / 2
                else piece_hours
                    * least(difference_mw_start, difference_mw_end)
                    * least(difference_mw_start, difference_mw_end)
                    / (2 * (abs(difference_mw_start) + abs(difference_mw_end)))
            end
        ) as turned_down_mwh
    from pieces
    group by national_grid_bm_unit, period_start_utc, interval_from
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
    left join stretch_energy
        on stretches.national_grid_bm_unit = stretch_energy.national_grid_bm_unit
        and stretches.period_start_utc = stretch_energy.period_start_utc
        and stretches.interval_from = stretch_energy.interval_from
    where stretch_energy.national_grid_bm_unit is null
),

group_half_hours as (
    select
        stretches.fuel_group,
        stretches.period_start_utc,
        sum(stretch_energy.turned_up_mwh) as turned_up_mwh,
        coalesce(sum(stretch_energy.turned_up_mwh) filter (where stretches.so_flag), 0)
            as flagged_turned_up_mwh,
        sum(stretch_energy.turned_down_mwh) as turned_down_mwh,
        coalesce(sum(stretch_energy.turned_down_mwh) filter (where stretches.so_flag), 0)
            as flagged_turned_down_mwh
    from stretches
    inner join stretch_energy
        on stretches.national_grid_bm_unit = stretch_energy.national_grid_bm_unit
        and stretches.period_start_utc = stretch_energy.period_start_utc
        and stretches.interval_from = stretch_energy.interval_from
    group by stretches.fuel_group, stretches.period_start_utc
),

-- The half-hours read: one row each, with the energy the wind units planned.
wind_plan_half_hours as (
    select
        commitments.period_start_utc,
        commitments.settlement_date,
        sum(commitments.pn_mwh) as planned_mwh
    from {{ ref('fct_commitments') }} as commitments
    inner join {{ ref('int_elexon__bm_unit_cohort') }} as cohort
        on commitments.national_grid_bm_unit = cohort.national_grid_bm_unit
    cross join first_stretch
    where cohort.fuel_group = 'wind'
        and commitments.coverage_status = 'complete'
        and commitments.period_start_utc >= first_stretch.period_start_utc
{% if read_until is not none %}
        and commitments.period_start_utc <= timestamptz '{{ read_until }}'
{% endif %}
    group by commitments.period_start_utc, commitments.settlement_date
),

-- Bands are on the exact energy: 50 MWh in a half-hour is 100 MW.
half_hours as (
    select
        wind_plan_half_hours.period_start_utc,
        date_trunc('month', wind_plan_half_hours.settlement_date)::date as settlement_month,
        wind_plan_half_hours.planned_mwh,
        coalesce(wind.turned_down_mwh, 0) as wind_turned_down_mwh,
        coalesce(wind.flagged_turned_down_mwh, 0) as wind_flagged_turned_down_mwh,
        case
            when coalesce(wind.flagged_turned_down_mwh, 0) <= 0 then '0 none'
            when wind.flagged_turned_down_mwh < 50 then '1 under 100 MW'
            when wind.flagged_turned_down_mwh < 250 then '2 100 MW to under 500 MW'
            when wind.flagged_turned_down_mwh < 500 then '3 500 MW to under 1 GW'
            when wind.flagged_turned_down_mwh < 1000 then '4 1 GW to under 2 GW'
            else '5 2 GW or more'
        end as wind_turn_down_band
    from wind_plan_half_hours
    left join group_half_hours as wind
        on wind_plan_half_hours.period_start_utc = wind.period_start_utc
        and wind.fuel_group = 'wind'
),

by_month as (
    select
        1 as section,
        settlement_month,
        null::text as wind_turn_down_band,
        'wind' as fuel_group,
        'wind turned down' as item,
        count(*) as half_hours,
        count(*) filter (where wind_flagged_turned_down_mwh > 0)
            as half_hours_with_flagged_wind_turn_down,
        count(*) filter (where wind_flagged_turned_down_mwh >= 50)
            as half_hours_at_100_mw_or_more,
        count(*) filter (where wind_flagged_turned_down_mwh >= 500)
            as half_hours_at_1_gw_or_more,
        null::numeric as turned_up_mwh,
        null::numeric as flagged_turned_up_mwh,
        round(sum(wind_turned_down_mwh), 3) as turned_down_mwh,
        round(sum(wind_flagged_turned_down_mwh), 3) as flagged_turned_down_mwh,
        round(sum(planned_mwh), 3) as planned_mwh,
        round(100 * sum(wind_flagged_turned_down_mwh) / nullif(sum(planned_mwh), 0), 2)
            as flagged_turned_down_pct_of_planned,
        min(period_start_utc) as first_half_hour,
        max(period_start_utc) as last_half_hour,
        null::bigint as stretches,
        null::numeric as level_energy_mwh
    from half_hours
    group by settlement_month
),

all_months as (
    select
        2 as section,
        null::date as settlement_month,
        null::text as wind_turn_down_band,
        'wind' as fuel_group,
        'wind turned down, all months' as item,
        count(*) as half_hours,
        count(*) filter (where wind_flagged_turned_down_mwh > 0)
            as half_hours_with_flagged_wind_turn_down,
        count(*) filter (where wind_flagged_turned_down_mwh >= 50)
            as half_hours_at_100_mw_or_more,
        count(*) filter (where wind_flagged_turned_down_mwh >= 500)
            as half_hours_at_1_gw_or_more,
        null::numeric as turned_up_mwh,
        null::numeric as flagged_turned_up_mwh,
        round(sum(wind_turned_down_mwh), 3) as turned_down_mwh,
        round(sum(wind_flagged_turned_down_mwh), 3) as flagged_turned_down_mwh,
        round(sum(planned_mwh), 3) as planned_mwh,
        round(100 * sum(wind_flagged_turned_down_mwh) / nullif(sum(planned_mwh), 0), 2)
            as flagged_turned_down_pct_of_planned,
        min(period_start_utc) as first_half_hour,
        max(period_start_utc) as last_half_hour,
        null::bigint as stretches,
        null::numeric as level_energy_mwh
    from half_hours
),

band_half_hours as (
    select
        wind_turn_down_band,
        count(*) as half_hours
    from half_hours
    group by wind_turn_down_band
),

by_band as (
    select
        3 as section,
        null::date as settlement_month,
        half_hours.wind_turn_down_band,
        group_half_hours.fuel_group,
        'energy turned up and down in the band''s half-hours' as item,
        band_half_hours.half_hours,
        null::bigint as half_hours_with_flagged_wind_turn_down,
        null::bigint as half_hours_at_100_mw_or_more,
        null::bigint as half_hours_at_1_gw_or_more,
        round(sum(group_half_hours.turned_up_mwh), 3) as turned_up_mwh,
        round(sum(group_half_hours.flagged_turned_up_mwh), 3) as flagged_turned_up_mwh,
        round(sum(group_half_hours.turned_down_mwh), 3) as turned_down_mwh,
        round(sum(group_half_hours.flagged_turned_down_mwh), 3) as flagged_turned_down_mwh,
        null::numeric as planned_mwh,
        null::numeric as flagged_turned_down_pct_of_planned,
        null::timestamptz as first_half_hour,
        null::timestamptz as last_half_hour,
        null::bigint as stretches,
        null::numeric as level_energy_mwh
    from half_hours
    inner join band_half_hours
        on half_hours.wind_turn_down_band = band_half_hours.wind_turn_down_band
    inner join group_half_hours
        on half_hours.period_start_utc = group_half_hours.period_start_utc
    group by
        half_hours.wind_turn_down_band,
        group_half_hours.fuel_group,
        band_half_hours.half_hours
),

coverage as (
    select
        4 as section,
        null::date as settlement_month,
        null::text as wind_turn_down_band,
        fuel_group,
        reason as item,
        null::bigint as half_hours,
        null::bigint as half_hours_with_flagged_wind_turn_down,
        null::bigint as half_hours_at_100_mw_or_more,
        null::bigint as half_hours_at_1_gw_or_more,
        null::numeric as turned_up_mwh,
        null::numeric as flagged_turned_up_mwh,
        null::numeric as turned_down_mwh,
        null::numeric as flagged_turned_down_mwh,
        null::numeric as planned_mwh,
        null::numeric as flagged_turned_down_pct_of_planned,
        null::timestamptz as first_half_hour,
        null::timestamptz as last_half_hour,
        count(*) as stretches,
        round(sum(abs(instructed_mwh)), 3) as level_energy_mwh
    from left_out
    group by fuel_group, reason

    union all

    select
        4,
        null,
        null,
        group_half_hours.fuel_group,
        'energy in half-hours with no complete wind PN capture',
        count(distinct group_half_hours.period_start_utc),
        null,
        null,
        null,
        round(sum(group_half_hours.turned_up_mwh), 3),
        round(sum(group_half_hours.flagged_turned_up_mwh), 3),
        round(sum(group_half_hours.turned_down_mwh), 3),
        round(sum(group_half_hours.flagged_turned_down_mwh), 3),
        null,
        null,
        null,
        null,
        null,
        null
    from group_half_hours
    left join half_hours
        on group_half_hours.period_start_utc = half_hours.period_start_utc
    where half_hours.period_start_utc is null
    group by group_half_hours.fuel_group
)

select
    section,
    settlement_month,
    wind_turn_down_band,
    fuel_group,
    item,
    half_hours,
    half_hours_with_flagged_wind_turn_down,
    half_hours_at_100_mw_or_more,
    half_hours_at_1_gw_or_more,
    turned_up_mwh,
    flagged_turned_up_mwh,
    turned_down_mwh,
    flagged_turned_down_mwh,
    planned_mwh,
    flagged_turned_down_pct_of_planned,
    first_half_hour,
    last_half_hour,
    stretches,
    level_energy_mwh
from by_month
union all
select
    section,
    settlement_month,
    wind_turn_down_band,
    fuel_group,
    item,
    half_hours,
    half_hours_with_flagged_wind_turn_down,
    half_hours_at_100_mw_or_more,
    half_hours_at_1_gw_or_more,
    turned_up_mwh,
    flagged_turned_up_mwh,
    turned_down_mwh,
    flagged_turned_down_mwh,
    planned_mwh,
    flagged_turned_down_pct_of_planned,
    first_half_hour,
    last_half_hour,
    stretches,
    level_energy_mwh
from all_months
union all
select
    section,
    settlement_month,
    wind_turn_down_band,
    fuel_group,
    item,
    half_hours,
    half_hours_with_flagged_wind_turn_down,
    half_hours_at_100_mw_or_more,
    half_hours_at_1_gw_or_more,
    turned_up_mwh,
    flagged_turned_up_mwh,
    turned_down_mwh,
    flagged_turned_down_mwh,
    planned_mwh,
    flagged_turned_down_pct_of_planned,
    first_half_hour,
    last_half_hour,
    stretches,
    level_energy_mwh
from by_band
union all
select
    section,
    settlement_month,
    wind_turn_down_band,
    fuel_group,
    item,
    half_hours,
    half_hours_with_flagged_wind_turn_down,
    half_hours_at_100_mw_or_more,
    half_hours_at_1_gw_or_more,
    turned_up_mwh,
    flagged_turned_up_mwh,
    turned_down_mwh,
    flagged_turned_down_mwh,
    planned_mwh,
    flagged_turned_down_pct_of_planned,
    first_half_hour,
    last_half_hour,
    stretches,
    level_energy_mwh
from coverage
order by section, settlement_month, wind_turn_down_band, fuel_group, item
