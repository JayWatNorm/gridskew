{{ config(group='analysis') }}

-- Q6: how late are unplanned outages published? Reading rule (research
-- questions): the median and 90th-percentile lag by outage class, where the
-- lag is the first publication's publish_time minus its event_start_time
-- (decision 022) and the class is the one the notice was first published
-- under. REMIT's "without delay" duty is not judged from this: the data
-- cannot show when the participant knew.
--
-- Population: unavailability notices whose current status is not
-- Dismissed, whose first stored publication is on or after
-- remit_q6_first_publication_from and whose first-published event start is
-- on or after 22 August 2024, when the history begins. Before the burn-in
-- date many stored "first" publications are later revisions of notices
-- first published before the history; an event that began before the
-- history may have been published before it too. Both exclusions read
-- dates only, never the lag.
--
-- Section 1 is the headline by class, with n the notices that have a lag
-- and the range of the population's first publications; section 2 splits
-- it by the current event type; section 3 counts every notice once by the
-- first reason it is left out (first, a first publication after
-- analysis_read_until), then one count inside the population that excludes
-- nothing. Run once, by hand, after the rule is recorded.

{% set read_until = var('analysis_read_until') %}

with notices as (
    select
        mrid,
        message_type,
        event_status,
        event_type,
        first_unavailability_type,
        unavailability_type,
        first_published_at,
        first_event_start_time,
        publication_lag_seconds,
        case
{% if read_until is not none %}
            when first_published_at > timestamptz '{{ read_until }}'
                then '0 first published after analysis_read_until'
{% endif %}
            when message_type is distinct from 'UnavailabilitiesOfElectricityFacilities'
                then '1 other market information'
            when event_status = 'Dismissed'
                then '2 dismissed'
            when first_published_at < timestamptz '{{ var("remit_q6_first_publication_from") }}'
                then '3 first published before the burn-in date'
            when first_event_start_time < timestamptz '2024-08-22 00:00:00+00'
                then '4 event starts before the history'
            else '5 in the population'
        end as coverage_reason
    from {{ ref('int_remit__notices') }}
),

population as (
    select *
    from notices
    where coverage_reason = '5 in the population'
),

headline as (
    select
        1 as section,
        first_unavailability_type as outage_class,
        null::text as event_type,
        null::text as item,
        count(publication_lag_seconds) as notices,
        round((percentile_cont(0.5) within group (order by publication_lag_seconds) / 60)::numeric, 1)
            as median_lag_minutes,
        round((percentile_cont(0.9) within group (order by publication_lag_seconds) / 60)::numeric, 1)
            as p90_lag_minutes,
        min(first_published_at) as first_published_from,
        max(first_published_at) as first_published_to
    from population
    group by first_unavailability_type
),

by_event_type as (
    select
        2 as section,
        first_unavailability_type as outage_class,
        event_type,
        null::text as item,
        count(publication_lag_seconds) as notices,
        round((percentile_cont(0.5) within group (order by publication_lag_seconds) / 60)::numeric, 1)
            as median_lag_minutes,
        round((percentile_cont(0.9) within group (order by publication_lag_seconds) / 60)::numeric, 1)
            as p90_lag_minutes,
        min(first_published_at) as first_published_from,
        max(first_published_at) as first_published_to
    from population
    group by first_unavailability_type, event_type
),

coverage as (
    select
        3 as section,
        null::text as outage_class,
        null::text as event_type,
        coverage_reason as item,
        count(*) as notices,
        null::numeric as median_lag_minutes,
        null::numeric as p90_lag_minutes,
        null::timestamptz as first_published_from,
        null::timestamptz as first_published_to
    from notices
    group by coverage_reason

    union all

    select
        3,
        null,
        null,
        '6 in the population: class changed after the first publication',
        count(*),
        null,
        null,
        null,
        null
    from population
    where unavailability_type is distinct from first_unavailability_type
)

select * from headline
union all
select * from by_event_type
union all
select * from coverage
order by section, outage_class, event_type, item
