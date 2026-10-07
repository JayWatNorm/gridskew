{{ config(group='analysis', access='private') }}

-- One row per forecast half-hour: its forecast trajectory under the forecast
-- drift reading rule (docs/carbon-forecast-trajectory.md), and beside it the
-- same trajectory "as published", with no poll excluded.
--
-- Every sign and limit test uses the exact horizon in seconds (3,600 to the
-- hour). A rounded horizon would let a poll made a fraction of a second after
-- the half-hour starts pass as a forecast of it.

with slot_polls as (
    select
        period_start,
        retrieved_at,
        forecast,
        horizon_seconds,
        is_partial_poll,
        is_counted_poll
    from {{ ref('int_carbon_forecast__revisions') }}
    where is_first_poll_in_slot
),

poll_sets as (
    select
        'reading_rule' as poll_set,
        period_start,
        retrieved_at,
        forecast,
        horizon_seconds
    from slot_polls
    where is_counted_poll

    union all

    select
        'partial_polls_excluded' as poll_set,
        period_start,
        retrieved_at,
        forecast,
        horizon_seconds
    from slot_polls
    where not is_partial_poll

    union all

    select
        'as_published' as poll_set,
        period_start,
        retrieved_at,
        forecast,
        horizon_seconds
    from slot_polls
),

trajectories as (
    -- A negative horizon is a poll made after the half-hour started. It counts
    -- as a slot and is never the final forecast.
    select
        poll_set,
        period_start,
        count(*) as slot_count,
        max(horizon_seconds) as first_horizon_seconds,
        min(horizon_seconds) filter (where horizon_seconds >= 0)
            as final_horizon_seconds,
        (array_agg(forecast order by horizon_seconds desc))[1] as first_forecast,
        (
            array_agg(forecast order by abs(horizon_seconds - 24 * 3600), retrieved_at)
                filter (where abs(horizon_seconds - 24 * 3600) <= 1800)
        )[1] as forecast_24h,
        (
            array_agg(forecast order by abs(horizon_seconds - 4 * 3600), retrieved_at)
                filter (where abs(horizon_seconds - 4 * 3600) <= 1800)
        )[1] as forecast_4h,
        (
            array_agg(forecast order by horizon_seconds)
                filter (where horizon_seconds >= 0)
        )[1] as final_forecast
    from poll_sets
    group by poll_set, period_start
),

judged_trajectories as (
    select
        poll_set,
        period_start,
        slot_count,
        first_horizon_seconds,
        final_horizon_seconds,
        first_forecast,
        forecast_24h,
        forecast_4h,
        final_forecast,
        final_forecast - first_forecast as drift,
        coalesce(
            slot_count >= {{ var('forecast_trajectory_min_slots') }}
            and first_horizon_seconds
                >= {{ var('forecast_trajectory_first_horizon_hours') }} * 3600
            and final_horizon_seconds
                <= {{ var('forecast_trajectory_final_horizon_hours') }} * 3600,
            false
        ) as has_complete_trajectory
    from trajectories
)

select
    as_published.period_start,
    coalesce(reading_rule.slot_count, 0) as counted_slots,
    round(reading_rule.first_horizon_seconds / 3600, 4) as first_horizon_hours,
    round(reading_rule.final_horizon_seconds / 3600, 4) as final_horizon_hours,
    reading_rule.first_forecast,
    reading_rule.forecast_24h,
    reading_rule.forecast_4h,
    reading_rule.final_forecast,
    reading_rule.drift,
    coalesce(reading_rule.has_complete_trajectory, false)
        as has_complete_trajectory,
    coalesce(partial_polls_excluded.has_complete_trajectory, false)
        as has_complete_trajectory_before_no_change_exclusion,
    as_published.slot_count as as_published_slots,
    as_published.drift as as_published_drift,
    as_published.has_complete_trajectory as as_published_has_complete_trajectory
from judged_trajectories as as_published
left join judged_trajectories as reading_rule
    on as_published.period_start = reading_rule.period_start
    and reading_rule.poll_set = 'reading_rule'
left join judged_trajectories as partial_polls_excluded
    on as_published.period_start = partial_polls_excluded.period_start
    and partial_polls_excluded.poll_set = 'partial_polls_excluded'
where as_published.poll_set = 'as_published'
