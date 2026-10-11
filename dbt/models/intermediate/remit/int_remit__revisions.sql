{{ config(group='analysis', access='private') }}

-- Every stored publication of a REMIT notice, in publication order under
-- decision 022: publish_time, then created_time, then revision_number.
-- Revision numbers are the participant's own counter and repeat and fall
-- (decision 019), so they only break the last tie. A publication is in force
-- from its publish_time until the next publication's; two publications with
-- the same publish_time give the earlier one an empty validity, because it
-- was never in force alone. The notice as known at t is the row with
-- valid_from <= t and (valid_to is null or t < valid_to).
--
-- Each publication names its unit by its own identifiers: an asset_id that
-- is the Elexon ID of exactly one registry unit, otherwise an affected_unit
-- that is a National Grid ID. A correction that names another unit therefore
-- changes the unit only from the correction's publish_time. The registry is
-- the latest extract, not the one in force when the notice was published.

with ordered as (
    select
        mrid,
        revision_number,
        publish_time,
        created_time,
        row_number() over publication_sequence as publication_order,
        lead(publish_time) over publication_sequence as next_publish_time,
        count(*) over (partition by mrid) as publication_count,
        message_type,
        event_type,
        unavailability_type,
        event_status,
        asset_id,
        affected_unit,
        fuel_type,
        normal_capacity,
        available_capacity,
        unavailable_capacity,
        event_start_time,
        event_end_time
    from {{ ref('stg_elexon__remit') }}
    window publication_sequence as (
        partition by mrid
        order by publish_time, created_time, revision_number
    )
),

registry as (
    select
        national_grid_bm_unit,
        elexon_bm_unit
    from {{ ref('int_elexon__bm_unit_ids') }}
),

asset_matches as (
    select
        ordered.mrid,
        ordered.publication_order,
        count(*) as matched_units,
        min(registry.national_grid_bm_unit) as national_grid_bm_unit
    from ordered
    inner join registry
        on ordered.asset_id = registry.elexon_bm_unit
    group by ordered.mrid, ordered.publication_order
),

affected_unit_matches as (
    select
        ordered.mrid,
        ordered.publication_order,
        registry.national_grid_bm_unit
    from ordered
    inner join registry
        on ordered.affected_unit = registry.national_grid_bm_unit
)

select
    ordered.mrid,
    ordered.revision_number,
    ordered.publish_time,
    ordered.created_time,
    ordered.publication_order,
    ordered.publish_time as valid_from,
    ordered.next_publish_time as valid_to,
    ordered.publication_order = ordered.publication_count as is_current,
    ordered.message_type,
    ordered.event_type,
    ordered.unavailability_type,
    ordered.event_status,
    ordered.asset_id,
    ordered.affected_unit,
    ordered.fuel_type,
    ordered.normal_capacity,
    ordered.available_capacity,
    ordered.unavailable_capacity,
    ordered.event_start_time,
    ordered.event_end_time,
    case
        when asset_matches.matched_units = 1 then 'asset_id'
        when affected_unit_matches.national_grid_bm_unit is not null then 'affected_unit'
        when asset_matches.matched_units > 1 then 'ambiguous'
        else 'unmapped'
    end as mapping_rule,
    case
        when asset_matches.matched_units = 1 then asset_matches.national_grid_bm_unit
        else affected_unit_matches.national_grid_bm_unit
    end as national_grid_bm_unit
from ordered
left join asset_matches
    on ordered.mrid = asset_matches.mrid
    and ordered.publication_order = asset_matches.publication_order
left join affected_unit_matches
    on ordered.mrid = affected_unit_matches.mrid
    and ordered.publication_order = affected_unit_matches.publication_order
