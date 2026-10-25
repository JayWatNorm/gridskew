{{ config(group='analysis', access='private') }}

-- For each unit and settlement half-hour, whether an outage of that unit was
-- already published at the half-hour's start (Q5). A half-hour starting at t
-- has a known outage when a publication that names the unit (by its own
-- identifiers) is a production unavailability, is the notice's publication
-- in force at t (ordered by publish_time, then created_time, then
-- revision_number), is Active, and its event overlaps the half-hour: it
-- starts before t + 30 minutes and ends after t (no end means it has not
-- ended). Only half-hours up to now are listed.
-- Candidate half-hours are enumerated generously from each publication's
-- window and the five conditions are then applied exactly.

with mapped_publications as (
    select
        mrid,
        national_grid_bm_unit,
        valid_from,
        valid_to,
        unavailability_type,
        event_start_time,
        event_end_time
    from {{ ref('int_remit__revisions') }}
    where mapping_rule in ('asset_id', 'affected_unit')
        and message_type = 'UnavailabilitiesOfElectricityFacilities'
        and event_type = 'Production unavailability'
        and event_status = 'Active'
),

candidate_periods as (
    select
        mapped_publications.*,
        candidates.period_start_utc
    from mapped_publications
    cross join lateral generate_series(
        date_bin(
            interval '30 minutes',
            greatest(valid_from, event_start_time),
            timestamptz '2000-01-01 00:00:00+00'
        ),
        least(
            coalesce(valid_to, current_timestamp),
            coalesce(event_end_time, current_timestamp),
            current_timestamp
        ),
        interval '30 minutes'
    ) as candidates (period_start_utc)
),

known as (
    select *
    from candidate_periods
    where valid_from <= period_start_utc
        and (valid_to is null or period_start_utc < valid_to)
        and event_start_time < period_start_utc + interval '30 minutes'
        and (event_end_time is null or event_end_time > period_start_utc)
        and period_start_utc <= current_timestamp
)

select
    national_grid_bm_unit,
    period_start_utc,
    bool_or(unavailability_type = 'Planned') as known_planned,
    bool_or(unavailability_type = 'Unplanned') as known_unplanned,
    case
        when bool_or(unavailability_type = 'Planned')
            and bool_or(unavailability_type = 'Unplanned') then 'both'
        when bool_or(unavailability_type = 'Planned') then 'planned'
        when bool_or(unavailability_type = 'Unplanned') then 'unplanned'
        else 'unclassified'
    end as known_class,
    count(distinct mrid) as notice_count
from known
group by national_grid_bm_unit, period_start_utc
