{{
    config(
        materialized='incremental',
        group='elexon_facts',
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

-- One row per National Grid BM unit and settlement period. Chooses one PN
-- capture, checks that its segments cover the period exactly once and
-- integrates the ramps to MWh. Registry evidence is attached in
-- fct_commitments.
--
-- Incremental: recompute only unit-periods with a capture since the stored
-- watermark minus capture_margin, from all of their captures. Raw PN is
-- insert-only, so this equals a full rebuild while every new row becomes
-- visible within the margin. Otherwise run a full refresh with `+`.

-- Staging is referenced directly in each CTE below, never through a shared
-- CTE: PostgreSQL materialises a CTE used twice, which would read all raw
-- history before the touched-key filter.

{% if is_incremental() %}
with touched as (
    select distinct national_grid_bm_unit, settlement_date, settlement_period
    from {{ ref('stg_elexon__pn') }}
    where retrieved_at > {{ capture_watermark(this) }} - {{ capture_margin() }}
),

-- Fetch by the declared key, not by time, so segments outside the period are
-- still found and make the period invalid.
segments as (
    select
        source_rows.national_grid_bm_unit,
        source_rows.bm_unit,
        source_rows.settlement_date,
        source_rows.settlement_period,
        source_rows.period_start_utc,
        source_rows.time_from,
        source_rows.time_to,
        source_rows.level_from,
        source_rows.level_to,
        source_rows.retrieved_at
    from {{ ref('stg_elexon__pn') }} as source_rows
    inner join touched
        on source_rows.national_grid_bm_unit = touched.national_grid_bm_unit
        and source_rows.settlement_date = touched.settlement_date
        and source_rows.settlement_period = touched.settlement_period
),
{% else %}
with segments as (
    select
        national_grid_bm_unit,
        bm_unit,
        settlement_date,
        settlement_period,
        period_start_utc,
        time_from,
        time_to,
        level_from,
        level_to,
        retrieved_at
    from {{ ref('stg_elexon__pn') }}
),
{% endif %}

-- A segment repeats an earlier capture when the same unit-period already has
-- an identical segment with an earlier retrieved_at.
marked as (
    select
        *,
        count(*) over (
            partition by
                national_grid_bm_unit, settlement_date, settlement_period,
                time_from, time_to, level_from, level_to
            order by retrieved_at
        ) > 1 as repeats_earlier
    from segments
),

-- An echo capture only repeats segments already captured. Daily polls return
-- the segment touching the requested start, so the next day's poll re-sends
-- the end of the previous day's last period.
captures as (
    select
        *,
        bool_and(repeats_earlier) over (
            partition by
                national_grid_bm_unit, settlement_date, settlement_period,
                retrieved_at
        ) as is_echo_capture
    from marked
),

-- Use the latest capture that is not an echo; the first capture never is one.
-- last_captured_at covers every capture, echoes included, so the watermark
-- advances with each poll.
chosen as (
    select
        *,
        max(retrieved_at) filter (where not is_echo_capture) over (
            partition by national_grid_bm_unit, settlement_date, settlement_period
        ) as chosen_retrieved_at,
        max(retrieved_at) over (
            partition by national_grid_bm_unit, settlement_date, settlement_period
        ) as last_captured_at
    from captures
),

chosen_segments as (
    select
        *,
        lag(time_to) over (
            partition by national_grid_bm_unit, settlement_date, settlement_period
            order by time_from
        ) as previous_time_to
    from chosen
    where retrieved_at = chosen_retrieved_at
),

periods as (
    select
        national_grid_bm_unit,
        settlement_date,
        settlement_period,
        period_start_utc,
        retrieved_at as pn_retrieved_at,
        max(last_captured_at) as last_captured_at,
        min(bm_unit) as source_bm_unit,
        -- Segments of one capture should carry one Elexon ID; more than one
        -- makes the mapping a conflict rather than silently taking the min.
        count(distinct bm_unit) > 1 as has_conflicting_source_bm_units,
        count(*) as segment_count,
        count(*) filter (
            where time_to <= time_from
                or time_from < period_start_utc
                or time_to > period_start_utc + interval '30 minutes'
        ) as invalid_segments,
        count(*) filter (where previous_time_to < time_from) as gap_count,
        count(*) filter (where previous_time_to > time_from) as overlap_count,
        min(time_from) = period_start_utc
            and max(time_to) = period_start_utc + interval '30 minutes'
            as spans_period,
        -- Trapezoid per segment: mean of the end-point MW times hours.
        sum(
            (level_from + level_to) / 2.0
            * extract(epoch from time_to - time_from) / 3600
        ) as integrated_mwh
    from chosen_segments
    group by
        national_grid_bm_unit,
        settlement_date,
        settlement_period,
        period_start_utc,
        retrieved_at
),

statuses as (
    select
        *,
        case
            -- A period number outside the day (for example 47 on a 46-period
            -- spring day) has no start time; every check below would be NULL.
            when period_start_utc is null or invalid_segments > 0 then 'invalid'
            when gap_count > 0 or overlap_count > 0 or not spans_period
                then 'incomplete'
            else 'complete'
        end as coverage_status
    from periods
)

select
    national_grid_bm_unit,
    settlement_date,
    settlement_period,
    period_start_utc,
    case when coverage_status = 'complete' then integrated_mwh end as pn_mwh,
    coverage_status,
    segment_count,
    pn_retrieved_at,
    source_bm_unit,
    has_conflicting_source_bm_units,
    last_captured_at
from statuses
