-- Nightly missing-row check: every half-hour reached by the latest capture of
-- a ramp point captured in the last build's window (watermark minus
-- capture_margin, up to the watermark) has a row in the intervals table.
-- Returns the missing half-hours. A half-hour that only an older capture of
-- the ramp point reached has no row after a full refresh and a placeholder
-- after an incremental run; neither is missing. Cheap: the literal watermark
-- lets it read only recent raw rows through the index. A row committed
-- between the build and this test can fail it once; the next build picks that
-- row up.

{{ config(group='analysis') }}

{% set watermark = capture_watermark(ref('int_elexon__instruction_intervals')) %}

with recent_ramp_points as (
    select distinct national_grid_bm_unit, acceptance_number, time_from
    from {{ ref('stg_elexon__boalf') }}
    where retrieved_at > {{ watermark }} - {{ capture_margin() }}
        and retrieved_at <= {{ watermark }}
),

latest_captures as (
    select
        captures.national_grid_bm_unit,
        captures.time_from,
        captures.time_to,
        row_number() over (
            partition by
                captures.national_grid_bm_unit,
                captures.acceptance_number,
                captures.time_from
            order by captures.retrieved_at desc
        ) as capture_rank
    from {{ ref('stg_elexon__boalf') }} as captures
    inner join recent_ramp_points
        on captures.national_grid_bm_unit = recent_ramp_points.national_grid_bm_unit
        and captures.acceptance_number = recent_ramp_points.acceptance_number
        and captures.time_from = recent_ramp_points.time_from
),

recent_periods as (
    select distinct
        national_grid_bm_unit,
        half_hours.period_start_utc
    from latest_captures
    cross join lateral generate_series(
        date_bin(interval '30 minutes', time_from, timestamptz '2000-01-01 00:00:00+00'),
        time_to - interval '1 microsecond',
        interval '30 minutes'
    ) as half_hours (period_start_utc)
    where capture_rank = 1
        and time_to > time_from
)

select recent_periods.*
from recent_periods
left join {{ ref('int_elexon__instruction_intervals') }} as intervals
    on recent_periods.national_grid_bm_unit = intervals.national_grid_bm_unit
    and recent_periods.period_start_utc = intervals.period_start_utc
where intervals.national_grid_bm_unit is null
