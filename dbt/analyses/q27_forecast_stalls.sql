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
-- first poll. A stall's time block is that of its first poll, inside the
-- window or not. Read once, when the window holds forecast_stalls_days_needed
-- days and at least three slot polls (one fewer than the run of four) are
-- stored after analysis_read_until, so a stall running past the cutoff is
-- settled; polls after the cutoff are never counted.
--
-- Amended 31 October 2026, before the window opened:
-- - Partial poll. A poll is partial when it holds fewer half-hours than a
--   full poll on its schedule (forecast_poll_schedules): 97 on the schedule
--   that polled on the half-hour, up to and including the 10:30 poll of
--   10 September 2026, and 96 on the :05 and :35 schedule from 11:05 that
--   day, in force when the window opens. The model's fixed 96-row flag is
--   not read.
-- - Schedule change. Each schedule with slot polls in a window is reported
--   with its first and last slot poll, its full count and its basis. A
--   schedule that appears later is added to forecast_poll_schedules, with its
--   full count fixed from its own polls' row counts before the read, and the
--   result says so. Beside them, before the read, the slot polls in the
--   window by minute past their slot (UTC), so a change shows without a stall
--   being counted.
-- - Run lengths, also reported: runs of consecutive unchanged slot polls
--   (Q1's test; partial polls count; a slot with no poll does not end a run),
--   each in the schedule of its first poll, inside the window or not, in
--   bands of 1, 2, 3, 4-5, 6-11 and 12 or more by the run's polls inside the
--   window, with the runs cut at the window's start or cutoff.
-- - Confirmed freezes, reported apart: stalls holding at least one partial
--   poll inside the window, with their number and median and longest
--   durations on both measures. The other stalls are not called freezes.
-- - Time of day: the split is informed by the long freezes of August and
--   September 2026 and the stalls of 1 to 24 October 2026.
-- - The record before the window: the stalls, partial polls by schedule,
--   each schedule's first and last slot poll, and the confirmed freezes
--   among the slot polls from forecast_stalls_exploratory_from (17 August
--   2026) to forecast_stalls_exploratory_until (31 October 2026 23:30 UTC),
--   exploratory and not part of the result, shown beside it at the read.
-- Until the read only section 1 has values. No forecast value is read.

{% set read_until = analysis_read_until() %}
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
{% set run_bands = [
    {'position': 1, 'label': '1 poll', 'shortest': 1},
    {'position': 2, 'label': '2 polls', 'shortest': 2},
    {'position': 3, 'label': '3 polls', 'shortest': 3},
    {'position': 4, 'label': '4-5 polls', 'shortest': 4},
    {'position': 5, 'label': '6-11 polls', 'shortest': 6},
    {'position': 6, 'label': '12 or more polls', 'shortest': 12},
] %}

with slot_polls as (
    select distinct
        poll_slot,
        retrieved_at,
        poll_row_count,
        is_unchanged_from_previous_slot,
        is_no_change_poll
    from {{ ref('int_carbon_forecast__revisions') }}
    where is_first_poll_in_slot
),

listed_schedules as (
    select
        schedule_order,
        schedule,
        full_poll_rows,
        basis
    from (
        values
{%- for schedule in forecast_poll_schedules() %}
            ({{ loop.index }}, '{{ schedule.label | replace("'", "''") }}', {{ schedule.full_poll_rows }}, '{{ schedule.basis | replace("'", "''") }}')
                {{- ',' if not loop.last }}
{%- endfor %}
    ) as listed (schedule_order, schedule, full_poll_rows, basis)
),

-- Each slot poll's schedule, and whether it is partial on that schedule.
scheduled_polls as (
    select
        poll_slot,
        retrieved_at,
        is_unchanged_from_previous_slot,
        is_no_change_poll,
        {{ forecast_poll_schedule('retrieved_at') }} as schedule,
        poll_row_count < {{ forecast_schedule_full_poll_rows('retrieved_at') }} as is_partial_poll
    from slot_polls
),

-- Each stall shares a run key: the number of changed polls before it. Each
-- run of unchanged polls shares one too.
numbered_polls as (
    select
        poll_slot,
        retrieved_at,
        schedule,
        is_partial_poll,
        is_unchanged_from_previous_slot,
        is_no_change_poll,
        count(*) filter (where not is_no_change_poll) over (
            order by poll_slot rows unbounded preceding
        ) as run_key,
        count(*) filter (where not is_unchanged_from_previous_slot) over (
            order by poll_slot rows unbounded preceding
        ) as unchanged_run_key
    from scheduled_polls
),

whole_stalls as (
    select run_key, min(poll_slot) as first_slot, max(poll_slot) as last_slot
    from numbered_polls
    where is_no_change_poll
    group by run_key
),

-- A run belongs to the schedule of its first poll, inside the window or not.
whole_unchanged_runs as (
    select
        unchanged_run_key,
        min(poll_slot) as first_slot,
        max(poll_slot) as last_slot,
        (array_agg(schedule order by poll_slot))[1] as schedule
    from numbered_polls
    where is_unchanged_from_previous_slot
    group by unchanged_run_key
),

{% for window in windows %}
{{ window.name }}_polls as (
    select
        poll_slot,
        retrieved_at,
        schedule,
        is_partial_poll,
        is_unchanged_from_previous_slot,
        is_no_change_poll,
        run_key,
        unchanged_run_key
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
        run_key,
        polls,
        partial_polls,
        stall_first_slot,
        is_cut_at_start,
        is_cut_at_end,
        partial_polls > 0 as is_confirmed_freeze,
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
            as cut_at_end,
        (select count(*) from {{ window.name }}_stall_durations where is_confirmed_freeze)
            as confirmed_freezes,
        (select percentile_cont(0.5) within group (order by poll_minutes)
            from {{ window.name }}_stall_durations where is_confirmed_freeze)
            as confirmed_median_poll_minutes,
        (select max(poll_minutes) from {{ window.name }}_stall_durations where is_confirmed_freeze)
            as confirmed_longest_poll_minutes,
        (select percentile_cont(0.5) within group (order by elapsed_minutes)
            from {{ window.name }}_stall_durations where is_confirmed_freeze)
            as confirmed_median_elapsed_minutes,
        (select max(elapsed_minutes) from {{ window.name }}_stall_durations where is_confirmed_freeze)
            as confirmed_longest_elapsed_minutes
),

{{ window.name }}_schedules as (
    select
        listed_schedules.schedule_order,
        listed_schedules.schedule,
        listed_schedules.full_poll_rows,
        listed_schedules.basis,
        count(polls.poll_slot) as slot_polls,
        count(*) filter (where polls.is_partial_poll) as partial_polls,
        min(polls.retrieved_at) as first_poll,
        max(polls.retrieved_at) as last_poll
    from listed_schedules
    left join {{ window.name }}_polls as polls
        on listed_schedules.schedule = polls.schedule
    group by
        listed_schedules.schedule_order,
        listed_schedules.schedule,
        listed_schedules.full_poll_rows,
        listed_schedules.basis
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
        result_window.first_slot,
        result_window.last_slot,
        result_window.slot_polls,
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

-- Runs of unchanged polls with a poll in the window, banded by their polls
-- inside it.
result_runs as (
    select
        polls.unchanged_run_key,
        count(*) as polls_in_window,
        min(whole.schedule) as schedule,
        min(whole.first_slot) < timestamptz '{{ windows[0].from }}' as is_cut_at_start,
{% if read_until is not none %}
        max(whole.last_slot) > timestamptz '{{ read_until }}' as is_cut_at_end
{% else %}
        false as is_cut_at_end
{% endif %}
    from result_polls as polls
    inner join whole_unchanged_runs as whole
        on polls.unchanged_run_key = whole.unchanged_run_key
    where polls.is_unchanged_from_previous_slot
    group by polls.unchanged_run_key
),

result_banded_runs as (
    select
        schedule,
        case
{%- for band in run_bands | reverse %}
            when polls_in_window >= {{ band.shortest }} then '{{ band.label }}'
{%- endfor %}
        end as band,
        is_cut_at_start,
        is_cut_at_end
    from result_runs
),

listed_bands as (
    select
        band_position,
        band
    from (
        values
{%- for band in run_bands %}
            ({{ band.position }}, '{{ band.label }}'){{ ',' if not loop.last }}
{%- endfor %}
    ) as listed (band_position, band)
),

result_band_counts as (
    select
        listed_schedules.schedule_order,
        listed_schedules.schedule,
        listed_bands.band_position,
        listed_bands.band,
        count(runs.band) as runs,
        count(*) filter (where runs.is_cut_at_start) as cut_at_start,
        count(*) filter (where runs.is_cut_at_end) as cut_at_end
    from listed_schedules
    cross join listed_bands
    left join result_banded_runs as runs
        on listed_schedules.schedule = runs.schedule
        and listed_bands.band = runs.band
    group by
        listed_schedules.schedule_order,
        listed_schedules.schedule,
        listed_bands.band_position,
        listed_bands.band
),

result_minutes_past_slot as (
    select
        floor(extract(epoch from retrieved_at - poll_slot) / 60)::integer as minutes_past_slot,
        count(*) as slot_polls
    from result_polls
    group by floor(extract(epoch from retrieved_at - poll_slot) / 60)::integer
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
    union all
    select 1, 9 + minutes_past_slot,
        'slot polls made ' || minutes_past_slot || ' minutes past their slot (UTC)',
        slot_polls, null, false
    from result_minutes_past_slot
{% for window in windows %}
{% set is_result = window.name == 'result' %}
{% set stall_section = 2 if is_result else 5 %}
{% set partial_section = 3 if is_result else 5 %}
{% set partial_offset = 0 if is_result else 20 %}
{% set label = '' if is_result else 'exploratory and not part of the result: ' %}
    union all
    select {{ stall_section }}, 1, '{{ label }}stalls', stalls, null, true
    from {{ window.name }}_summary
    union all
    select {{ stall_section }}, 2, '{{ label }}median duration, poll-equivalent minutes',
        median_poll_minutes::numeric, null, true
    from {{ window.name }}_summary
    union all
    select {{ stall_section }}, 3, '{{ label }}longest duration, poll-equivalent minutes',
        longest_poll_minutes, null, true
    from {{ window.name }}_summary
    union all
    select {{ stall_section }}, 4, '{{ label }}median duration, elapsed minutes',
        median_elapsed_minutes::numeric, null, true
    from {{ window.name }}_summary
    union all
    select {{ stall_section }}, 5, '{{ label }}longest duration, elapsed minutes',
        longest_elapsed_minutes, null, true
    from {{ window.name }}_summary
    union all
    select {{ stall_section }}, 6, '{{ label }}share of slot polls inside a stall',
        round(stall_polls::numeric / nullif(slot_polls, 0), 6), null, true
    from {{ window.name }}_summary
    union all
    select {{ stall_section }}, 7, '{{ label }}stalls cut at the window start',
        cut_at_start, null, true
    from {{ window.name }}_summary
    union all
    select {{ stall_section }}, 8, '{{ label }}stalls cut at the window end',
        cut_at_end, null, true
    from {{ window.name }}_summary
    union all
    select {{ stall_section }}, 9,
        '{{ label }}confirmed freezes: stalls holding a partial poll inside the window',
        confirmed_freezes, null, true
    from {{ window.name }}_summary
    union all
    select {{ stall_section }}, 10,
        '{{ label }}confirmed freezes: median duration, poll-equivalent minutes',
        confirmed_median_poll_minutes::numeric, null, true
    from {{ window.name }}_summary
    union all
    select {{ stall_section }}, 11,
        '{{ label }}confirmed freezes: longest duration, poll-equivalent minutes',
        confirmed_longest_poll_minutes, null, true
    from {{ window.name }}_summary
    union all
    select {{ stall_section }}, 12,
        '{{ label }}confirmed freezes: median duration, elapsed minutes',
        confirmed_median_elapsed_minutes::numeric, null, true
    from {{ window.name }}_summary
    union all
    select {{ stall_section }}, 13,
        '{{ label }}confirmed freezes: longest duration, elapsed minutes',
        confirmed_longest_elapsed_minutes, null, true
    from {{ window.name }}_summary
    union all
    select {{ partial_section }}, {{ partial_offset }} + 1, '{{ label }}partial polls',
        partial_polls, null, true
    from {{ window.name }}_summary
    union all
    select {{ partial_section }}, {{ partial_offset }} + 2,
        '{{ label }}share of slot polls that are partial',
        round(partial_polls::numeric / nullif(slot_polls, 0), 6), null, true
    from {{ window.name }}_summary
    union all
    select {{ partial_section }}, {{ partial_offset }} + 3, '{{ label }}partial polls inside a stall',
        partial_polls_in_stalls, null, true
    from {{ window.name }}_summary
    union all
    select {{ partial_section }}, {{ partial_offset }} + 3 + schedule_order,
        '{{ label }}partial polls, schedule ' || schedule
            || ' (fewer than ' || full_poll_rows || ' half-hours)',
        partial_polls, null, true
    from {{ window.name }}_schedules
{% if not is_result %}
    union all
    select 5, 40, '{{ label }}slot polls', slot_polls, null, true
    from {{ window.name }}_summary
{% endif %}
    union all
    select {{ 7 if is_result else 5 }}, {{ 0 if is_result else 40 }} + schedule_order,
        '{{ label }}capture schedule ' || schedule,
        slot_polls,
        'first slot poll ' || to_char(first_poll at time zone 'UTC', 'YYYY-MM-DD HH24:MI:SS.MS')
            || ' UTC; last slot poll '
            || to_char(last_poll at time zone 'UTC', 'YYYY-MM-DD HH24:MI:SS.MS')
            || ' UTC; full poll ' || full_poll_rows || ' half-hours; basis: ' || basis,
        true
    from {{ window.name }}_schedules
    where slot_polls > 0
{% endfor %}
    union all
    select 4, row_number() over (order by label)::int,
        'stalls starting ' || label || ' (split informed by the long freezes of August and '
            || 'September 2026 and the stalls of 1 to 24 October 2026)',
        stalls, null, true
    from result_blocks
    union all
    select 6, schedule_order * 10 + band_position,
        'runs of unchanged slot polls, schedule ' || schedule || ': ' || band || ' in the window',
        runs,
        'cut at the window start: ' || cut_at_start || '; cut at the window end: ' || cut_at_end,
        true
    from result_band_counts
)

select
    output_rows.section,
    output_rows.item,
    case when output_rows.is_gated and not gate.is_reached then null else output_rows.value end as value,
    case when output_rows.is_gated and not gate.is_reached then null else output_rows.detail end as detail
from output_rows
cross join gate
order by output_rows.section, output_rows.position
