-- Warning: a WINDFOR publish day or FUELHH WIND settlement date the daily
-- capture holds that is not complete. A publish day is complete with at least
-- 8 issues, each holding a point for every hour from 21:00 UK time the day
-- before to 21:00 UK time two days after; a settlement date with exactly
-- periods 1 to its 46, 48 or 50. ingestion/elexon/wind_poller.py holds the
-- same rule; tests/adhoc/check_database_writes.py checks that both name the
-- same days. Checks the days from wind_capture_from and the last 30 UTC days
-- up to two days ago: the last two may not be captured when the nightly runs.
-- Counts only. Reads only rows stored since the day before the first day,
-- through the retrieved_at index: no row of a day in the range is stored
-- earlier.

{{ config(severity='warn') }}

with checked_range as (
    select
        greatest(
            '{{ var("wind_capture_from") }}'::date,
            (now() at time zone 'UTC')::date - 30
        ) as first_day,
        (now() at time zone 'UTC')::date - 2 as last_day
),

checked_days as (
    select
        generate_series(
            first_day::timestamp, last_day::timestamp, interval '1 day'
        )::date as day
    from checked_range
),

windfor_issues as (
    select
        (publish_time at time zone 'UTC')::date as publish_day,
        count(*) as points
    from {{ ref('stg_elexon__windfor') }}
    where
        retrieved_at >= (
            select (first_day - 1)::timestamp at time zone 'UTC'
            from checked_range
        )
        and publish_time >= (
            select first_day::timestamp at time zone 'UTC' from checked_range
        )
        and publish_time < (
            select (last_day + 1)::timestamp at time zone 'UTC'
            from checked_range
        )
    group by publish_time
),

windfor_days as (
    select
        publish_day,
        count(*) as issues,
        min(points) as smallest_issue_points
    from windfor_issues
    group by publish_day
),

windfor_checked as (
    select
        'WINDFOR' as dataset,
        checked_days.day,
        coalesce(windfor_days.issues, 0) as found,
        8 as expected,
        windfor_days.smallest_issue_points,
        1 + (
            extract(
                epoch from (
                    ((checked_days.day + 2) + time '21:00')
                    at time zone 'Europe/London'
                    - ((checked_days.day - 1) + time '21:00')
                    at time zone 'Europe/London'
                )
            ) / 3600
        )::integer as expected_points
    from checked_days
    left join windfor_days
        on checked_days.day = windfor_days.publish_day
),

fuelhh_dates as (
    select
        settlement_date,
        count(distinct settlement_period) as periods,
        min(settlement_period) as smallest_period,
        max(settlement_period) as largest_period
    from {{ ref('stg_elexon__fuelhh') }}
    where
        fuel_type = 'WIND'
        and retrieved_at >= (
            select (first_day - 1)::timestamp at time zone 'UTC'
            from checked_range
        )
        and settlement_date >= (select first_day from checked_range)
        and settlement_date <= (select last_day from checked_range)
    group by settlement_date
),

fuelhh_checked as (
    select
        'FUELHH' as dataset,
        checked_days.day,
        coalesce(fuelhh_dates.periods, 0) as found,
        {{ settlement_period_count('checked_days.day') }} as expected,
        fuelhh_dates.smallest_period,
        fuelhh_dates.largest_period
    from checked_days
    left join fuelhh_dates
        on checked_days.day = fuelhh_dates.settlement_date
)

select
    dataset,
    day,
    found,
    expected,
    smallest_issue_points,
    expected_points
from windfor_checked
where
    found < expected
    or smallest_issue_points < expected_points

union all

select
    dataset,
    day,
    found,
    expected,
    null::bigint as smallest_issue_points,
    null::integer as expected_points
from fuelhh_checked
where
    found != expected
    or smallest_period != 1
    or largest_period != expected
