{{
    config(
        materialized='incremental',
        group='analysis',
        access='private',
        incremental_strategy='delete+insert',
        unique_key=['national_grid_bm_unit', 'settlement_date', 'settlement_period'],
        on_schema_change='fail',
        indexes=[
            {
                'columns': [
                    'national_grid_bm_unit', 'settlement_date', 'settlement_period'
                ],
                'unique': True
            },
            {'columns': ['last_captured_at']}
        ]
    )
}}

-- One row per cohort unit and settlement period that has a PN. The cohort
-- (int_elexon__bm_unit_cohort) is every registry unit with a published fuel
-- type; everything else has no physical baseline to compare with.
--
-- Expected output is the PN, with the instructed level in its place for the
-- minutes an acceptance was in force: expected = PN energy outside the
-- instructed minutes + instructed energy. Positive unexplained_shortfall_mwh
-- means the unit delivered less than expected. Nothing is dropped: a period
-- that cannot be judged keeps its row and says why in determinability.
--
-- ABSVD (QAS: balancing services settled outside the balancing mechanism)
-- sits beside these columns and changes none of them: Settlement adds it to
-- the expected volume, so unexplained_after_absvd_mwh adds it to the
-- unexplained energy. Each unit-period's ABSVD comes from the latest capture
-- that holds it; a captured date's unit-period that no capture holds has 0,
-- and a date never captured has null.
--
-- Incremental: recompute only unit-periods with a PN, metered or acceptance
-- capture since the stored watermark minus capture_margin, every period of a
-- settlement date with a QAS capture since then, and every period
-- of a unit whose fuel type or Elexon ID in the cohort differs from the ones
-- stored, or that has PN but no rows yet (it joined the cohort). Each source
-- is insert-only, so this equals a full rebuild while every new row becomes
-- visible within the margin. Two registry events need a full refresh: a unit
-- that loses its fuel type keeps its rows (the analyses join the cohort, so
-- it leaves the results at once), and another unit starting or stopping to
-- share this unit's Elexon ID changes the metered mapping without touching
-- this unit's registry row; warn_cohort_elexon_id_shared says so each night.

with cohort as (
    select national_grid_bm_unit, elexon_bm_unit, fuel_type, fuel_group
    from {{ ref('int_elexon__bm_unit_cohort') }}
),

{% if is_incremental() %}
{% set changed_units = changed_cohort_units(
    this, ref('int_elexon__bm_unit_cohort'), ref('fct_commitments')
) %}
-- Each source is filtered on its period table's indexed last_captured_at:
-- the latest capture of any row of the period, never earlier than the
-- capture the fact view reports, so no recent capture is missed. Keyed like
-- the table, on the settlement date and period, so a period with no UTC
-- start (a period number beyond a 46-period day) is recomputed too.
touched as (
    select
        commitments.national_grid_bm_unit,
        commitments.settlement_date,
        commitments.settlement_period
    from {{ ref('fct_commitments') }} as commitments
    inner join cohort
        on commitments.national_grid_bm_unit = cohort.national_grid_bm_unit
    where commitments.last_captured_at
        > {{ capture_watermark(this) }} - {{ capture_margin() }}

    union

    -- Every recent metered capture for the cohort unit's Elexon ID,
    -- whatever its mapping: a late run that arrives with another National
    -- Grid ID turns the mapping to conflict, and that must reach the row.
    select
        cohort.national_grid_bm_unit,
        generation.settlement_date,
        generation.settlement_period
    from {{ ref('fct_generation') }} as generation
    inner join cohort
        on generation.bm_unit = cohort.elexon_bm_unit
    where generation.last_captured_at
        > {{ capture_watermark(this) }} - {{ capture_margin() }}

    union

    select
        commitments.national_grid_bm_unit,
        commitments.settlement_date,
        commitments.settlement_period
    from {{ ref('int_elexon__instruction_intervals') }} as intervals
    inner join cohort
        on intervals.national_grid_bm_unit = cohort.national_grid_bm_unit
    inner join {{ ref('fct_commitments') }} as commitments
        on commitments.national_grid_bm_unit = intervals.national_grid_bm_unit
        -- Follows from the equal start times; it lets the join use the
        -- period table's index on unit and settlement date.
        and commitments.settlement_date
            = (intervals.period_start_utc at time zone 'Europe/London')::date
        and commitments.period_start_utc = intervals.period_start_utc
    where intervals.last_captured_at
        > {{ capture_watermark(this) }} - {{ capture_margin() }}

    union

    -- Every cohort unit-period of a settlement date with a new QAS capture: a
    -- capture can change any unit of its date, and a date's first capture
    -- turns every unit's ABSVD from unknown to a value or 0. Unit by unit, so
    -- the period table's index on unit and settlement date serves the join.
    select
        commitments.national_grid_bm_unit,
        commitments.settlement_date,
        commitments.settlement_period
    from cohort
    cross join (
        select distinct settlement_date
        from {{ ref('stg_elexon__qas') }}
        where retrieved_at > {{ capture_watermark(this) }} - {{ capture_margin() }}
    ) as captured
    inner join {{ ref('fct_commitments') }} as commitments
        on commitments.national_grid_bm_unit = cohort.national_grid_bm_unit
        and commitments.settlement_date = captured.settlement_date
    {% if changed_units %}

    union

    -- A unit whose fuel type or Elexon ID changed in the registry, or that
    -- has PN but no rows yet, gets every period recomputed, so its fuel
    -- group and metered mapping follow the registry.
    select
        commitments.national_grid_bm_unit,
        commitments.settlement_date,
        commitments.settlement_period
    from {{ ref('fct_commitments') }} as commitments
    where commitments.national_grid_bm_unit in (
        {%- for unit in changed_units %}
        '{{ unit | replace("'", "''") }}'{{ ',' if not loop.last }}
        {%- endfor %}
    )
    {% endif %}
),

commitments as (
    select
        commitments.national_grid_bm_unit,
        commitments.settlement_date,
        commitments.settlement_period,
        commitments.period_start_utc,
        commitments.pn_mwh,
        commitments.coverage_status,
        commitments.pn_retrieved_at,
        commitments.last_captured_at,
        cohort.elexon_bm_unit,
        cohort.fuel_type,
        cohort.fuel_group
    from {{ ref('fct_commitments') }} as commitments
    inner join cohort
        on commitments.national_grid_bm_unit = cohort.national_grid_bm_unit
    inner join touched
        on commitments.national_grid_bm_unit = touched.national_grid_bm_unit
        and commitments.settlement_date = touched.settlement_date
        and commitments.settlement_period = touched.settlement_period
),
{% else %}
commitments as (
    select
        commitments.national_grid_bm_unit,
        commitments.settlement_date,
        commitments.settlement_period,
        commitments.period_start_utc,
        commitments.pn_mwh,
        commitments.coverage_status,
        commitments.pn_retrieved_at,
        commitments.last_captured_at,
        cohort.elexon_bm_unit,
        cohort.fuel_type,
        cohort.fuel_group
    from {{ ref('fct_commitments') }} as commitments
    inner join cohort
        on commitments.national_grid_bm_unit = cohort.national_grid_bm_unit
),
{% endif %}

-- The settlement dates of the rows being built that QAS has captured, with
-- the time of each date's latest capture.
qas_dates as (
    select
        qas.settlement_date,
        max(qas.retrieved_at) as latest_retrieved_at
    from {{ ref('stg_elexon__qas') }} as qas
{% if is_incremental() %}
    where qas.settlement_date in (select settlement_date from commitments)
{% endif %}
    group by qas.settlement_date
),

-- Each unit-period's ABSVD from the latest capture that holds it: a unit
-- missing from a later capture keeps the value of the one before (nothing
-- shows yet that a later capture is complete; the resubmission analysis
-- counts such units).
qas_latest as (
    select distinct on (
        qas.national_grid_bm_unit, qas.settlement_date, qas.settlement_period
    )
        qas.national_grid_bm_unit,
        qas.settlement_date,
        qas.settlement_period,
        qas.qas_mwh
    from {{ ref('stg_elexon__qas') }} as qas
    inner join commitments
        on qas.national_grid_bm_unit = commitments.national_grid_bm_unit
        and qas.settlement_date = commitments.settlement_date
        and qas.settlement_period = commitments.settlement_period
    order by
        qas.national_grid_bm_unit,
        qas.settlement_date,
        qas.settlement_period,
        qas.retrieved_at desc
),

-- Metered rows are read by the Elexon ID the registry gives the cohort unit
-- and kept only when the fact view maps them back to the same unit. More
-- than one metered row for a period (two Elexon IDs for one unit) is
-- reported, never summed.
metered as (
    select
        commitments.national_grid_bm_unit,
        commitments.period_start_utc,
        generation.latest_quantity_mwh,
        generation.latest_run_code,
        generation.latest_retrieved_at,
        -- Across all of the period's metered rows, so the watermark also
        -- passes a capture of a row that is not the one kept.
        max(generation.last_captured_at) over (
            partition by commitments.national_grid_bm_unit, commitments.period_start_utc
        ) as last_captured_at,
        generation.first_quantity_mwh,
        generation.first_run_code,
        count(*) over (
            partition by commitments.national_grid_bm_unit, commitments.period_start_utc
        ) as metered_row_count,
        row_number() over (
            partition by commitments.national_grid_bm_unit, commitments.period_start_utc
            order by generation.latest_retrieved_at desc, generation.bm_unit
        ) as recency_rank
    from commitments
    inner join {{ ref('fct_generation') }} as generation
        on generation.bm_unit = commitments.elexon_bm_unit
        and generation.period_start_utc = commitments.period_start_utc
{% if is_incremental() %}
        -- The settlement date and period follow from the equal start times;
        -- they let the few touched keys use the period table's unique index.
        -- Not on a full refresh: the planner counts the three equal keys as
        -- independent, expects almost no matching rows and picks a nested
        -- loop over the whole table.
        and generation.settlement_date = commitments.settlement_date
        and generation.settlement_period = commitments.settlement_period
{% endif %}
        and generation.mapping_status = 'mapped'
        and generation.mapped_national_grid_bm_unit = commitments.national_grid_bm_unit
),

-- The stretches of each half-hour in which an acceptance was in force.
intervals as (
    select
        commitments.national_grid_bm_unit,
        commitments.period_start_utc,
        intervals.interval_from,
        intervals.interval_to,
        intervals.acceptance_number,
        intervals.acceptance_time,
        intervals.so_flag,
        intervals.instructed_mwh,
        intervals.last_captured_at
    from commitments
    inner join {{ ref('int_elexon__instruction_intervals') }} as intervals
        on intervals.national_grid_bm_unit = commitments.national_grid_bm_unit
        and intervals.period_start_utc = commitments.period_start_utc
        -- A placeholder row marks a half-hour with no stretch left.
        and intervals.interval_from is not null
),

-- The PN energy inside those stretches, from the segments of the capture
-- fct_commitments chose, so it can be taken out and the instructed energy
-- put in its place.
pn_inside_intervals as (
    select
        intervals.national_grid_bm_unit,
        intervals.period_start_utc,
        sum(
            {{ ramp_mwh(
                'segments.level_from', 'segments.level_to',
                'segments.time_from', 'segments.time_to',
                'intervals.interval_from', 'intervals.interval_to'
            ) }}
        ) as pn_in_instructed_mwh
    from intervals
    inner join commitments
        on commitments.national_grid_bm_unit = intervals.national_grid_bm_unit
        and commitments.period_start_utc = intervals.period_start_utc
    inner join {{ ref('stg_elexon__pn') }} as segments
        on segments.national_grid_bm_unit = commitments.national_grid_bm_unit
        and segments.settlement_date = commitments.settlement_date
        and segments.settlement_period = commitments.settlement_period
        and segments.retrieved_at = commitments.pn_retrieved_at
        -- A segment with no duration has no energy and no slope; the PN
        -- model marks such a capture invalid, so its expected output is null.
        and segments.time_to > segments.time_from
        and segments.time_from < intervals.interval_to
        and segments.time_to > intervals.interval_from
    group by intervals.national_grid_bm_unit, intervals.period_start_utc
),

instruction as (
    select
        national_grid_bm_unit,
        period_start_utc,
        count(distinct acceptance_number) as acceptance_count,
        max(acceptance_time) as latest_acceptance_time,
        bool_or(so_flag) as any_so_flag,
        sum(extract(epoch from interval_to - interval_from)) as instructed_seconds,
        sum(instructed_mwh) as instructed_mwh,
        max(last_captured_at) as last_captured_at
    from intervals
    group by national_grid_bm_unit, period_start_utc
),

joined as (
    select
        commitments.national_grid_bm_unit,
        commitments.settlement_date,
        commitments.settlement_period,
        commitments.period_start_utc,
        commitments.elexon_bm_unit,
        commitments.fuel_type,
        commitments.fuel_group,
        commitments.pn_mwh,
        commitments.coverage_status as pn_coverage_status,
        metered.latest_quantity_mwh as metered_mwh,
        metered.latest_run_code as metered_run_code,
        metered.first_quantity_mwh as first_metered_mwh,
        metered.first_run_code,
        coalesce(metered.metered_row_count, 0) as metered_row_count,
        coalesce(instruction.acceptance_count, 0) as acceptance_count,
        instruction.latest_acceptance_time,
        instruction.any_so_flag,
        coalesce(instruction.instructed_seconds, 0)::integer as instructed_seconds,
        instruction.instructed_mwh,
        round(pn_inside_intervals.pn_in_instructed_mwh, 6) as pn_in_instructed_mwh,
        case
            when instruction.instructed_seconds = 1800 then 'fully_instructed'
            when instruction.instructed_seconds > 0 then 'partially_instructed'
            else 'uninstructed'
        end as instruction_status,
        round(
            case
                when instruction.instructed_seconds = 1800
                    then instruction.instructed_mwh
                when commitments.coverage_status = 'complete'
                    then commitments.pn_mwh
                        - coalesce(pn_inside_intervals.pn_in_instructed_mwh, 0)
                        + coalesce(instruction.instructed_mwh, 0)
            end,
            6
        ) as expected_mwh,
        round(
            case
                when qas_dates.settlement_date is not null
                    then coalesce(qas_latest.qas_mwh, 0)
            end,
            6
        ) as absvd_mwh,
        -- The same capture times the touched set filters on, so a
        -- recomputed period's watermark moves past every capture that
        -- touched it (a later capture of a lower-order run included).
        greatest(
            commitments.last_captured_at,
            metered.last_captured_at,
            instruction.last_captured_at,
            qas_dates.latest_retrieved_at
        ) as last_captured_at
    from commitments
    left join metered
        on commitments.national_grid_bm_unit = metered.national_grid_bm_unit
        and commitments.period_start_utc = metered.period_start_utc
        and metered.recency_rank = 1
    left join instruction
        on commitments.national_grid_bm_unit = instruction.national_grid_bm_unit
        and commitments.period_start_utc = instruction.period_start_utc
    left join pn_inside_intervals
        on commitments.national_grid_bm_unit = pn_inside_intervals.national_grid_bm_unit
        and commitments.period_start_utc = pn_inside_intervals.period_start_utc
    left join qas_dates
        on commitments.settlement_date = qas_dates.settlement_date
    left join qas_latest
        on commitments.national_grid_bm_unit = qas_latest.national_grid_bm_unit
        and commitments.settlement_date = qas_latest.settlement_date
        and commitments.settlement_period = qas_latest.settlement_period
)

-- Energies are rounded to six decimal places, like the BOALF model's.
select
    *,
    round(expected_mwh - metered_mwh, 6) as unexplained_shortfall_mwh,
    round(pn_mwh - metered_mwh, 6) as deviation_from_pn_mwh,
    round(pn_mwh - expected_mwh, 6) as instructed_deviation_mwh,
    -- QAS is added to the expected volume (BSC Section T 4.3.2).
    round(expected_mwh - metered_mwh + absvd_mwh, 6) as unexplained_after_absvd_mwh,
    case
        when metered_row_count > 1 then 'ambiguous_metered'
        when metered_mwh is null then 'no_metered_value'
        when expected_mwh is null then 'pn_' || pn_coverage_status
        else 'determinable'
    end as determinability
from joined
