{{
    config(
        materialized='incremental',
        group='analysis',
        access='private',
        incremental_strategy='delete+insert',
        unique_key=['national_grid_bm_unit', 'period_start_utc'],
        on_schema_change='fail',
        indexes=[
            {'columns': ['national_grid_bm_unit', 'period_start_utc', 'interval_from'], 'unique': True},
            {'columns': ['last_captured_at']}
        ]
    )
}}

-- One row per National Grid BM unit, settlement half-hour and stretch of
-- time in which one acceptance is in force. Acceptances overlap: a later
-- acceptance supersedes an earlier one for the minutes they share and no
-- others, which is how the balancing mechanism settles them. Each half-hour
-- is cut at every instant an acceptance starts or stops, and each piece is
-- given to the most recently issued acceptance that covers it. Pieces no
-- acceptance covers have no row; the PN applies there.
--
-- Incremental: recompute every half-hour touched by any capture of a ramp
-- point captured since the stored watermark minus capture_margin, from all
-- of its unit's ramp points. The merge key is the half-hour, so a recomputed
-- half-hour replaces all of its stretches; a half-hour recomputed to no
-- stretch at all (an acceptance shortened by a later capture) keeps one row
-- with a null interval_from, so its old stretches are still replaced. Raw
-- BOALF is insert-only, so this equals a full rebuild while every new row
-- becomes visible within the margin.

{% if is_incremental() %}
-- A new capture can change any half-hour that any capture of its ramp point
-- touches: a shortened ramp point must release the half-hours it no longer
-- reaches.
with recent_ramp_points as (
    select
        national_grid_bm_unit,
        acceptance_number,
        time_from,
        -- The time of the latest capture of the ramp point: every half-hour
        -- any capture of it reached is stamped with this, so a half-hour the
        -- latest capture no longer reaches still shows as changed now.
        max(retrieved_at) as last_captured_at
    from {{ ref('stg_elexon__boalf') }}
    group by national_grid_bm_unit, acceptance_number, time_from
    having max(retrieved_at) > {{ capture_watermark(this) }} - {{ capture_margin() }}
),

touched_periods as (
    select
        captures.national_grid_bm_unit,
        half_hours.period_start_utc,
        max(recent_ramp_points.last_captured_at) as last_captured_at
    from {{ ref('stg_elexon__boalf') }} as captures
    inner join recent_ramp_points
        on captures.national_grid_bm_unit = recent_ramp_points.national_grid_bm_unit
        and captures.acceptance_number = recent_ramp_points.acceptance_number
        and captures.time_from = recent_ramp_points.time_from
    cross join lateral generate_series(
        date_bin(
            interval '30 minutes',
            captures.time_from,
            timestamptz '2000-01-01 00:00:00+00'
        ),
        captures.time_to - interval '1 microsecond',
        interval '30 minutes'
    ) as half_hours (period_start_utc)
    where captures.time_to > captures.time_from
    group by captures.national_grid_bm_unit, half_hours.period_start_utc
),

-- Every capture of every ramp point of a touched unit, so the latest capture
-- is chosen over all of them, not over the ones that still overlap.
ramp_points as (
    select
        source_rows.*,
        row_number() over (
            partition by
                source_rows.national_grid_bm_unit,
                source_rows.acceptance_number,
                source_rows.time_from
            order by source_rows.retrieved_at desc
        ) as capture_rank
    from {{ ref('stg_elexon__boalf') }} as source_rows
    inner join (select distinct national_grid_bm_unit from touched_periods) as units
        on source_rows.national_grid_bm_unit = units.national_grid_bm_unit
),
{% else %}
with ramp_points as (
    select
        *,
        row_number() over (
            partition by national_grid_bm_unit, acceptance_number, time_from
            order by retrieved_at desc
        ) as capture_rank
    from {{ ref('stg_elexon__boalf') }}
),
{% endif %}

latest_ramp_points as (
    select *
    from ramp_points
    where capture_rank = 1
        and time_to > time_from
),

ramp_point_periods as (
    select
        latest_ramp_points.*,
        half_hours.period_start_utc,
        greatest(time_from, half_hours.period_start_utc) as covered_from,
        least(time_to, half_hours.period_start_utc + interval '30 minutes')
            as covered_to
    from latest_ramp_points
    cross join lateral generate_series(
        date_bin(
            interval '30 minutes',
            latest_ramp_points.time_from,
            timestamptz '2000-01-01 00:00:00+00'
        ),
        latest_ramp_points.time_to - interval '1 microsecond',
        interval '30 minutes'
    ) as half_hours (period_start_utc)
    {% if is_incremental() %}
    inner join touched_periods
        on touched_periods.national_grid_bm_unit = latest_ramp_points.national_grid_bm_unit
        and touched_periods.period_start_utc = half_hours.period_start_utc
    {% endif %}
),

-- Every instant inside a half-hour where the set of acceptances in force
-- can change.
breakpoints as (
    select national_grid_bm_unit, period_start_utc, covered_from as at
    from ramp_point_periods
    union
    select national_grid_bm_unit, period_start_utc, covered_to
    from ramp_point_periods
),

pieces as (
    select
        national_grid_bm_unit,
        period_start_utc,
        at as interval_from,
        lead(at) over (
            partition by national_grid_bm_unit, period_start_utc
            order by at
        ) as interval_to
    from breakpoints
),

-- Each ramp point that covers a whole piece, ranked by how recently its
-- acceptance was issued. A piece lies inside one ramp point of each
-- acceptance that covers it, because the pieces were cut at every ramp
-- point's edges.
candidates as (
    select
        pieces.national_grid_bm_unit,
        pieces.period_start_utc,
        pieces.interval_from,
        pieces.interval_to,
        ramp_point_periods.acceptance_number,
        ramp_point_periods.acceptance_time,
        ramp_point_periods.so_flag,
        ramp_point_periods.level_from,
        ramp_point_periods.level_to,
        ramp_point_periods.time_from,
        ramp_point_periods.time_to,
        ramp_point_periods.retrieved_at,
        row_number() over (
            partition by
                pieces.national_grid_bm_unit, pieces.period_start_utc, pieces.interval_from
            order by
                ramp_point_periods.acceptance_time desc,
                ramp_point_periods.acceptance_number desc,
                ramp_point_periods.time_from desc
        ) as precedence,
        max(ramp_point_periods.retrieved_at) over (
            partition by pieces.national_grid_bm_unit, pieces.period_start_utc
        ) as last_captured_at
    from pieces
    inner join ramp_point_periods
        on ramp_point_periods.national_grid_bm_unit = pieces.national_grid_bm_unit
        and ramp_point_periods.period_start_utc = pieces.period_start_utc
        and ramp_point_periods.covered_from <= pieces.interval_from
        and ramp_point_periods.covered_to >= pieces.interval_to
    where pieces.interval_to > pieces.interval_from
)

select
    candidates.national_grid_bm_unit,
    candidates.period_start_utc,
    candidates.interval_from,
    candidates.interval_to,
    candidates.acceptance_number,
    candidates.acceptance_time,
    candidates.so_flag,
    round(
        {{ ramp_mwh(
            'candidates.level_from', 'candidates.level_to',
            'candidates.time_from', 'candidates.time_to',
            'candidates.interval_from', 'candidates.interval_to'
        ) }},
        6
    ) as instructed_mwh,
    candidates.retrieved_at,
{% if is_incremental() %}
    -- The surviving stretches of a touched half-hour are stamped with the
    -- capture that touched it, like the placeholder would be.
    greatest(candidates.last_captured_at, touched_periods.last_captured_at)
        as last_captured_at
from candidates
inner join touched_periods
    on touched_periods.national_grid_bm_unit = candidates.national_grid_bm_unit
    and touched_periods.period_start_utc = candidates.period_start_utc
{% else %}
    candidates.last_captured_at
from candidates
{% endif %}
where candidates.precedence = 1

{% if is_incremental() %}
union all

-- A touched half-hour with no stretch left: the placeholder that replaces
-- its old rows.
select
    touched_periods.national_grid_bm_unit,
    touched_periods.period_start_utc,
    null as interval_from,
    null as interval_to,
    null as acceptance_number,
    null as acceptance_time,
    null as so_flag,
    null as instructed_mwh,
    null as retrieved_at,
    touched_periods.last_captured_at
from touched_periods
where not exists (
    select 1
    from candidates
    where candidates.national_grid_bm_unit = touched_periods.national_grid_bm_unit
        and candidates.period_start_utc = touched_periods.period_start_utc
)
{% endif %}
