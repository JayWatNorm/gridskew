{{ config(group='analysis') }}

-- The population of the forecast drift question
-- (docs/carbon-forecast-trajectory.md): how many half-hours have an outturn
-- and a complete forecast trajectory, when they fall, and how many each
-- exclusion removes. It reads no forecast value and no drift, so it can run
-- on any day. The verdict query runs once, on the first day this reports
-- 2,880 complete half-hours.

with half_hours as (
    select
        period_start,
        has_complete_trajectory,
        has_complete_trajectory_before_no_change_exclusion,
        as_published_has_complete_trajectory
    from {{ ref('int_carbon_error_by_period') }}
    where period_start >= timestamptz '2026-08-17 00:00:00+00'
),

complete_half_hours as (
    select period_start
    from half_hours
    where has_complete_trajectory
),

removed_by_partial_polls as (
    select period_start
    from half_hours
    where as_published_has_complete_trajectory
        and not has_complete_trajectory_before_no_change_exclusion
),

removed_by_no_change_polls as (
    select period_start
    from half_hours
    where has_complete_trajectory_before_no_change_exclusion
        and not has_complete_trajectory
)

select
    1 as section,
    'population' as breakdown,
    'complete half-hours (2,880 needed)' as item,
    count(*) as half_hours,
    min(period_start) as first_half_hour,
    max(period_start) as last_half_hour
from complete_half_hours

union all

select
    2 as section,
    'removed by an exclusion' as breakdown,
    'partial polls' as item,
    count(*) as half_hours,
    min(period_start) as first_half_hour,
    max(period_start) as last_half_hour
from removed_by_partial_polls

union all

select
    2 as section,
    'removed by an exclusion' as breakdown,
    'no-change polls' as item,
    count(*) as half_hours,
    min(period_start) as first_half_hour,
    max(period_start) as last_half_hour
from removed_by_no_change_polls

union all

select
    3 as section,
    'complete, by weekday (UTC)' as breakdown,
    to_char(period_start at time zone 'UTC', 'ID Dy') as item,
    count(*) as half_hours,
    min(period_start) as first_half_hour,
    max(period_start) as last_half_hour
from complete_half_hours
group by to_char(period_start at time zone 'UTC', 'ID Dy')

union all

select
    4 as section,
    'complete, by half-hour of the day (UTC)' as breakdown,
    to_char(period_start at time zone 'UTC', 'HH24:MI') as item,
    count(*) as half_hours,
    min(period_start) as first_half_hour,
    max(period_start) as last_half_hour
from complete_half_hours
group by to_char(period_start at time zone 'UTC', 'HH24:MI')

order by section, item
