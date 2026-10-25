{{ config(group='analysis') }}

-- Q27: how often, and for how long, does the published carbon-intensity
-- forecast stop changing? Reading rule (research questions, registered
-- 25 October 2026, before any poll in its window existed): describe only.
-- A stall is a run of consecutive slot polls flagged is_no_change_poll, Q1's
-- exclusion rule (partial polls count towards its run of four; a slot with
-- no poll does not end a run). The window holds slot polls from
-- forecast_stalls_from to analysis_read_until. Reported: the number of
-- stalls, median and longest duration both as polls x 30 minutes and as
-- elapsed time (first to last poll's slot plus 30 minutes), the share of
-- slot polls inside a stall, stalls cut at the window's edges, partial polls
-- and how many fall inside a stall, and stalls by the UTC block of their
-- first poll (a split informed by the stalls seen before registration).
-- A stall's time block is that of its first poll, inside the window or not.
-- Read once, when the window holds forecast_stalls_days_needed days and at
-- least three slot polls (one fewer than the run of four) are stored after
-- analysis_read_until, so a stall running past the cutoff is settled; polls
-- after the cutoff are never counted.
-- Until then only section 1 has values. Section 5 repeats sections 2 and 3
-- for the stalls seen before registration, labelled exploratory. No forecast
-- value is read.

{% set read_until = var('analysis_read_until') %}
{% set windows = [
    {
        'name': 'result',
        'from': var('forecast_stalls_from'),
        'until': read_until,
    },
    {
        'name': 'exploratory',
        'from': var('forecast_stalls_exploratory_from'),
        'until': var('forecast_stalls_exploratory_until'),
    },
] %}

with slot_polls as (
    select distinct poll_slot, is_partial_poll, is_no_change_poll
    from {{ ref('int_carbon_forecast__revisions') }}
    where is_first_poll_in_slot
),

-- Each stall shares a run key: the number of changed polls before it.
numbered_polls as (
    select
        poll_slot,
        is_partial_poll,
        is_no_change_poll,
        count(*) filter (where not is_no_change_poll) over (
            order by poll_slot rows unbounded preceding
        ) as run_key
    from slot_polls
),

whole_stalls as (
    select run_key, min(poll_slot) as first_slot, max(poll_slot) as last_slot
    from numbered_polls
    where is_no_change_poll
    group by run_key
),

{% for window in windows %}
{{ window.name }}_polls as (
    select *
    from numbered_polls
    where poll_slot >= timestamptz '{{ window.from }}'
{% if window.until is not none %}
        and poll_slot <= timestamptz '{{ window.until }}'
{% endif %}
),

{{ window.name }}_stalls as (
    select
        polls.run_key,
        count(*) as polls,
        count(*) filter (where polls.is_partial_poll) as partial_polls,
        min(polls.poll_slot) as first_slot,
        max(polls.poll_slot) as last_slot,
        min(whole.first_slot) as stall_first_slot,
        min(whole.first_slot) < timestamptz '{{ window.from }}' as is_cut_at_start,
{% if window.until is not none %}
        max(whole.last_slot) > timestamptz '{{ window.until }}' as is_cut_at_end
{% else %}
        false as is_cut_at_end
{% endif %}
    from {{ window.name }}_polls as polls
    inner join whole_stalls as whole on polls.run_key = whole.run_key
    where polls.is_no_change_poll
    group by polls.run_key
),

{{ window.name }}_stall_durations as (
    select
        *,
        polls * 30 as poll_minutes,
        (extract(epoch from last_slot - first_slot) / 60)::integer + 30 as elapsed_minutes
    from {{ window.name }}_stalls
),

{{ window.name }}_summary as (
    select
        (select count(*) from {{ window.name }}_polls) as slot_polls,
        (select count(*) from {{ window.name }}_polls where is_partial_poll) as partial_polls,
        (select count(*) from {{ window.name }}_stall_durations) as stalls,
        (select coalesce(sum(polls), 0) from {{ window.name }}_stall_durations) as stall_polls,
        (select coalesce(sum(partial_polls), 0) from {{ window.name }}_stall_durations)
            as partial_polls_in_stalls,
        (select percentile_cont(0.5) within group (order by poll_minutes)
            from {{ window.name }}_stall_durations) as median_poll_minutes,
        (select max(poll_minutes) from {{ window.name }}_stall_durations) as longest_poll_minutes,
        (select percentile_cont(0.5) within group (order by elapsed_minutes)
            from {{ window.name }}_stall_durations) as median_elapsed_minutes,
        (select max(elapsed_minutes) from {{ window.name }}_stall_durations)
            as longest_elapsed_minutes,
        (select count(*) from {{ window.name }}_stall_durations where is_cut_at_start)
            as cut_at_start,
        (select count(*) from {{ window.name }}_stall_durations where is_cut_at_end)
            as cut_at_end
),

{% endfor %}

result_window as (
    select
        min(poll_slot) as first_slot,
        max(poll_slot) as last_slot,
        count(*) as slot_polls
    from result_polls
),

result_coverage as (
    select
        result_window.*,
        coalesce(
            floor(
                extract(epoch from result_window.last_slot + interval '30 minutes'
                    - timestamptz '{{ windows[0].from }}') / 86400
            ),
            0
        ) as days_covered,
        coalesce(
            floor(
                extract(epoch from
{% if read_until is not none %}
                    timestamptz '{{ read_until }}'
{% else %}
                    result_window.last_slot
{% endif %}
                    - timestamptz '{{ windows[0].from }}') / 1800
            ) + 1 - result_window.slot_polls,
            0
        ) as slots_without_poll,
{% if read_until is not none %}
        (select count(*) from slot_polls where poll_slot > timestamptz '{{ read_until }}')
            >= {{ var('forecast_no_change_run_polls') }} - 1 as has_look_ahead
{% else %}
        false as has_look_ahead
{% endif %}
    from result_window
),

gate as (
    select
        days_covered >= {{ var('forecast_stalls_days_needed') }} and has_look_ahead
            as is_reached
    from result_coverage
),

result_blocks as (
    select
        blocks.label,
        count(stalls.run_key) as stalls
    from (
        values
            (0, '00:00-05:59 UTC'),
            (6, '06:00-11:59 UTC'),
            (12, '12:00-17:59 UTC'),
            (18, '18:00-23:59 UTC')
    ) as blocks (first_hour, label)
    left join result_stalls as stalls
        on extract(hour from stalls.stall_first_slot at time zone 'UTC') >= blocks.first_hour
        and extract(hour from stalls.stall_first_slot at time zone 'UTC') < blocks.first_hour + 6
    group by blocks.first_hour, blocks.label
),

output_rows as (
    select 1 as section, 1 as position, 'window start' as item,
        null::numeric as value, '{{ windows[0].from }}' as detail, false as is_gated
    union all
    select 1, 2, 'first slot poll', null,
        to_char(first_slot at time zone 'UTC', 'YYYY-MM-DD HH24:MI') || ' UTC', false
    from result_coverage
    union all
    select 1, 3, 'last slot poll', null,
        to_char(last_slot at time zone 'UTC', 'YYYY-MM-DD HH24:MI') || ' UTC', false
    from result_coverage
    union all
    select 1, 4, 'days covered ({{ var("forecast_stalls_days_needed") }} needed)',
        days_covered, null, false from result_coverage
    union all
    select 1, 5, 'slot polls', slot_polls, null, false from result_coverage
    union all
    select 1, 6, 'slots with no poll', slots_without_poll, null, false from result_coverage
    union all
    select 1, 7,
        'slot polls stored after analysis_read_until ({{ var("forecast_no_change_run_polls") - 1 }} needed)',
        null,
        case when has_look_ahead then 'yes' else 'no' end, false
    from result_coverage
    union all
    select 1, 8, 'read possible', null,
        case when is_reached then 'yes' else 'no' end, false
    from gate
{% for window in windows %}
{% set section = 2 if window.name == 'result' else 5 %}
{% set gated = 'true' if window.name == 'result' else 'false' %}
{% set label = '' if window.name == 'result' else 'exploratory, seen before registration: ' %}
    union all
    select {{ section }}, 1, '{{ label }}stalls', stalls, null, {{ gated }}
    from {{ window.name }}_summary
    union all
    select {{ section }}, 2, '{{ label }}median duration, poll-equivalent minutes',
        median_poll_minutes::numeric, null, {{ gated }}
    from {{ window.name }}_summary
    union all
    select {{ section }}, 3, '{{ label }}longest duration, poll-equivalent minutes',
        longest_poll_minutes, null, {{ gated }}
    from {{ window.name }}_summary
    union all
    select {{ section }}, 4, '{{ label }}median duration, elapsed minutes',
        median_elapsed_minutes::numeric, null, {{ gated }}
    from {{ window.name }}_summary
    union all
    select {{ section }}, 5, '{{ label }}longest duration, elapsed minutes',
        longest_elapsed_minutes, null, {{ gated }}
    from {{ window.name }}_summary
    union all
    select {{ section }}, 6, '{{ label }}share of slot polls inside a stall',
        round(stall_polls::numeric / nullif(slot_polls, 0), 6), null, {{ gated }}
    from {{ window.name }}_summary
    union all
    select {{ section }}, 7, '{{ label }}stalls cut at the window start',
        cut_at_start, null, {{ gated }}
    from {{ window.name }}_summary
    union all
    select {{ section }}, 8, '{{ label }}stalls cut at the window end',
        cut_at_end, null, {{ gated }}
    from {{ window.name }}_summary
    union all
    select {{ section + 1 if window.name == 'result' else section }},
        {{ 1 if window.name == 'result' else 9 }}, '{{ label }}partial polls',
        partial_polls, null, {{ gated }}
    from {{ window.name }}_summary
    union all
    select {{ section + 1 if window.name == 'result' else section }},
        {{ 2 if window.name == 'result' else 10 }},
        '{{ label }}share of slot polls that are partial',
        round(partial_polls::numeric / nullif(slot_polls, 0), 6), null, {{ gated }}
    from {{ window.name }}_summary
    union all
    select {{ section + 1 if window.name == 'result' else section }},
        {{ 3 if window.name == 'result' else 11 }}, '{{ label }}partial polls inside a stall',
        partial_polls_in_stalls, null, {{ gated }}
    from {{ window.name }}_summary
{% endfor %}
    union all
    select 4, row_number() over (order by label)::int,
        'stalls starting ' || label || ' (split informed by the exploratory stalls)',
        stalls, null, true
    from result_blocks
)

select
    output_rows.section,
    output_rows.item,
    case when output_rows.is_gated and not gate.is_reached then null else output_rows.value end as value,
    output_rows.detail
from output_rows
cross join gate
order by output_rows.section, output_rows.position
