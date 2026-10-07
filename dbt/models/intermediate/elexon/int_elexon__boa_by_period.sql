{{ config(group='analysis', access='private') }}

-- One row per unit, acceptance, ramp point and settlement half-hour that the
-- ramp point overlaps. instructed_mwh is the energy of the instructed MW
-- level inside that half-hour.

-- A daily poll returns the ramp points that start in its UTC day, both ends
-- included. An acceptance that crosses midnight is therefore spread over two
-- polls, and its midnight point is in both. The latest capture is chosen for
-- each ramp point, never for a whole acceptance.
with ramp_points as (
    select
        *,
        row_number() over (
            partition by national_grid_bm_unit, acceptance_number, time_from
            order by retrieved_at desc
        ) as capture_rank
    from {{ ref('stg_elexon__boalf') }}
),

-- A ramp point with no duration has no energy and no slope to interpolate.
latest_ramp_points as (
    select *
    from ramp_points
    where capture_rank = 1
        and time_to > time_from
),

-- Settlement half-hours start on the UTC hour and half-hour in every season,
-- so the first half-hour a ramp point touches is its start floored to 30
-- minutes. The series stops before time_to: a ramp point that ends on a
-- boundary does not reach the next half-hour.
ramp_point_periods as (
    select
        latest_ramp_points.*,
        half_hours.period_start_utc
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
)

select
    national_grid_bm_unit,
    bm_unit,
    acceptance_number,
    acceptance_time,
    period_start_utc,
    time_from as ramp_point_time_from,
    greatest(time_from, period_start_utc) as covered_from,
    least(time_to, period_start_utc + interval '30 minutes') as covered_to,
    round(
        {{ ramp_mwh(
            'level_from', 'level_to', 'time_from', 'time_to',
            'period_start_utc', "period_start_utc + interval '30 minutes'"
        ) }},
        6
    ) as instructed_mwh,
    so_flag,
    retrieved_at
from ramp_point_periods
