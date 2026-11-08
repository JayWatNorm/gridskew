-- Warning: a REMIT publish day holding a message stored more than two hours
-- after its publish time. The hourly read stores a message within about an
-- hour; a later one was found by the daily re-read, or by the hourly read
-- catching up after an outage. retrieved_at says when GridSkew stored it, not
-- when Elexon began serving it. Checks the UTC publish days from
-- remit_late_check_from and the last 30 UTC days up to yesterday. Today is the
-- UTC date, or the var remit_late_check_today when a check sets it. Counts
-- and seconds only.

{{ config(severity='warn') }}

with evaluation_day as (
    select
        {% if var('remit_late_check_today') is not none -%}
            '{{ var("remit_late_check_today") }}'::date
        {%- else -%}
            (now() at time zone 'UTC')::date
        {%- endif %} as day
),

checked_range as (
    select
        greatest(
            '{{ var("remit_late_check_from") }}'::date,
            evaluation_day.day - 30
        ) as first_day,
        evaluation_day.day - 1 as last_day
    from evaluation_day
),

messages as (
    select
        (publish_time at time zone 'UTC')::date as publish_day,
        retrieved_at - publish_time > interval '2 hours' as is_late,
        extract(epoch from retrieved_at - publish_time)::bigint as seconds_late
    from {{ ref('stg_elexon__remit') }}
    where
        publish_time >= (
            select first_day::timestamp at time zone 'UTC' from checked_range
        )
        and publish_time < (
            select (last_day + 1)::timestamp at time zone 'UTC'
            from checked_range
        )
)

select
    publish_day,
    count(*) filter (where is_late) as stored_late,
    count(*) as messages,
    min(seconds_late) filter (where is_late) as smallest_seconds_late,
    max(seconds_late) filter (where is_late) as largest_seconds_late
from messages
group by publish_day
having count(*) filter (where is_late) > 0
order by publish_day
