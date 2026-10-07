{{ config(group='analysis', access='private') }}

-- One row per forecast half-hour per poll, with the poll flags of the forecast
-- drift reading rule (docs/carbon-forecast-trajectory.md). No row is dropped:
-- the polls the rule counts are the rows where is_counted_poll is true.

with forecasts as (
    select
        period_start,
        retrieved_at,
        forecast,
        extract(epoch from (period_start - retrieved_at))::numeric
            as horizon_seconds
    from {{ ref('stg_carbon_intensity__forecast') }}
),

polls as (
    select
        retrieved_at,
        date_bin(
            interval '30 minutes',
            retrieved_at,
            timestamptz '2000-01-01 00:00:00+00'
        ) as poll_slot,
        count(*) as poll_row_count
    from forecasts
    group by retrieved_at
),

polls_ranked_in_slot as (
    select
        retrieved_at,
        poll_slot,
        poll_row_count,
        row_number() over (
            partition by poll_slot order by retrieved_at
        ) as rank_in_slot
    from polls
),

slot_polls as (
    -- The earliest poll of each slot, beside the slot poll before it.
    select
        retrieved_at,
        poll_slot,
        lag(retrieved_at) over (order by poll_slot) as previous_slot_retrieved_at
    from polls_ranked_in_slot
    where rank_in_slot = 1
),

slot_poll_differences as (
    select
        slot_polls.retrieved_at,
        slot_polls.poll_slot,
        count(previous_forecasts.period_start) as shared_half_hours,
        count(*) filter (
            where previous_forecasts.forecast <> forecasts.forecast
        ) as changed_half_hours
    from slot_polls
    inner join forecasts
        on slot_polls.retrieved_at = forecasts.retrieved_at
    left join forecasts as previous_forecasts
        on slot_polls.previous_slot_retrieved_at = previous_forecasts.retrieved_at
        and forecasts.period_start = previous_forecasts.period_start
    group by slot_polls.retrieved_at, slot_polls.poll_slot
),

slot_polls_compared as (
    select
        retrieved_at,
        poll_slot,
        shared_half_hours > 0 and changed_half_hours = 0
            as is_unchanged_from_previous_slot
    from slot_poll_differences
),

slot_polls_in_runs as (
    -- A run is one changed poll and the unchanged polls that follow it.
    select
        retrieved_at,
        is_unchanged_from_previous_slot,
        count(*) filter (where not is_unchanged_from_previous_slot) over (
            order by poll_slot
        ) as run_number
    from slot_polls_compared
),

slot_polls_with_run_length as (
    select
        retrieved_at,
        is_unchanged_from_previous_slot,
        count(*) filter (where is_unchanged_from_previous_slot) over (
            partition by run_number
        ) as unchanged_polls_in_run
    from slot_polls_in_runs
),

poll_flags as (
    -- A later poll of a slot is never compared, so it is never "unchanged".
    select
        polls_ranked_in_slot.retrieved_at,
        polls_ranked_in_slot.poll_slot,
        polls_ranked_in_slot.poll_row_count,
        polls_ranked_in_slot.rank_in_slot = 1 as is_first_poll_in_slot,
        polls_ranked_in_slot.poll_row_count
            < {{ var('forecast_full_poll_rows') }} as is_partial_poll,
        coalesce(slot_runs.is_unchanged_from_previous_slot, false)
            as is_unchanged_from_previous_slot,
        coalesce(
            slot_runs.is_unchanged_from_previous_slot
            and slot_runs.unchanged_polls_in_run
                >= {{ var('forecast_no_change_run_polls') }},
            false
        ) as is_no_change_poll
    from polls_ranked_in_slot
    left join slot_polls_with_run_length as slot_runs
        on polls_ranked_in_slot.retrieved_at = slot_runs.retrieved_at
)

select
    forecasts.period_start,
    forecasts.retrieved_at,
    forecasts.forecast,
    -- The rule reads the exact seconds. The rounded hours are for people.
    forecasts.horizon_seconds,
    round(forecasts.horizon_seconds / 3600, 4) as horizon_hours,
    poll_flags.poll_slot,
    poll_flags.poll_row_count,
    poll_flags.is_first_poll_in_slot,
    poll_flags.is_partial_poll,
    poll_flags.is_unchanged_from_previous_slot,
    poll_flags.is_no_change_poll,
    poll_flags.is_first_poll_in_slot
        and not poll_flags.is_partial_poll
        and not poll_flags.is_no_change_poll as is_counted_poll
from forecasts
inner join poll_flags
    on forecasts.retrieved_at = poll_flags.retrieved_at
