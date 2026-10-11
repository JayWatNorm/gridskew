{{ config(group='analysis', access='private') }}

-- One row per REMIT notice: its first publication, its current publication
-- (decision 022) and the BM unit it names. The publication lag is measured
-- from the first publication: its publish_time minus its event_start_time,
-- in whole seconds, positive when the notice was published after the event
-- began. The unit is the current publication's, mapped in
-- int_remit__revisions.

with first_publications as (
    select
        mrid,
        publish_time as first_published_at,
        event_start_time as first_event_start_time,
        unavailability_type as first_unavailability_type
    from {{ ref('int_remit__revisions') }}
    where publication_order = 1
),

current_publications as (
    select *
    from {{ ref('int_remit__revisions') }}
    where is_current
)

select
    current_publications.mrid,
    current_publications.publication_order as publication_count,
    first_publications.first_published_at,
    first_publications.first_event_start_time,
    first_publications.first_unavailability_type,
    extract(epoch from (
        first_publications.first_published_at
        - first_publications.first_event_start_time
    ))::bigint as publication_lag_seconds,
    current_publications.publish_time as current_published_at,
    current_publications.revision_number as current_revision_number,
    current_publications.message_type,
    current_publications.event_type,
    current_publications.unavailability_type,
    current_publications.event_status,
    current_publications.asset_id,
    current_publications.affected_unit,
    current_publications.fuel_type,
    current_publications.normal_capacity,
    current_publications.available_capacity,
    current_publications.unavailable_capacity,
    current_publications.event_start_time,
    current_publications.event_end_time,
    current_publications.mapping_rule,
    current_publications.national_grid_bm_unit
from current_publications
inner join first_publications
    on current_publications.mrid = first_publications.mrid
