{{ config(group='analysis') }}

-- The health read at the start of each GridSkew session (plan v3.5, B07),
-- run by hand in a read-only session. It reads sizes, rows captured per day
-- and dbt results, never a research-question value. Host free space and the
-- workstation backup's age are not in the database and are read separately.
--
-- Sections: 1, database size against the storage trigger (250 GB); 2, rows
-- captured per UTC day over the last 14 days for each source, a day with
-- none shown as 0 (each count filters on the source's capture time, indexed
-- except on the small REMIT table); 3, dbt nodes that did not pass, recorded
-- since health_since (default: the last 7 days).

{% set since = var('health_since', none) %}
{% set node_results = adapter.get_relation(
    database=target.database, schema=target.schema, identifier='dbt_node_results'
) %}

with window_start as (
    select date_trunc('day', now(), 'UTC') - interval '13 days' as starts_at
),

days as (
    select (day_start at time zone 'UTC')::date as captured_on
    from generate_series(
        (select starts_at from window_start), now(), interval '1 day'
    ) as day_start
),

captures as (
    select 'carbon forecast' as source, (retrieved_at at time zone 'UTC')::date as captured_on
    from {{ source('intensity', 'carbon_intensity_forecast') }}
    where retrieved_at >= (select starts_at from window_start)

    union all

    select 'carbon outturn', (retrieved_at at time zone 'UTC')::date
    from {{ source('intensity', 'carbon_intensity_outturn') }}
    where retrieved_at >= (select starts_at from window_start)

    union all

    select 'PN', (retrieved_at at time zone 'UTC')::date
    from {{ source('elexon', 'elexon_pn') }}
    where retrieved_at >= (select starts_at from window_start)

    union all

    select 'QPN', (retrieved_at at time zone 'UTC')::date
    from {{ source('elexon', 'elexon_qpn') }}
    where retrieved_at >= (select starts_at from window_start)

    union all

    select 'B1610', (retrieved_at at time zone 'UTC')::date
    from {{ source('elexon', 'elexon_b1610') }}
    where retrieved_at >= (select starts_at from window_start)

    union all

    select 'BOALF', (retrieved_at at time zone 'UTC')::date
    from {{ source('elexon', 'elexon_boalf') }}
    where retrieved_at >= (select starts_at from window_start)

    union all

    select 'REMIT', (retrieved_at at time zone 'UTC')::date
    from {{ source('elexon', 'elexon_remit') }}
    where retrieved_at >= (select starts_at from window_start)
),

sources as (
    select source
    from (
        values
            ('carbon forecast'), ('carbon outturn'), ('PN'), ('QPN'),
            ('B1610'), ('BOALF'), ('REMIT')
    ) as listed (source)
)

select
    1 as section,
    'database size, GB (trigger 250)' as item,
    null::date as captured_on,
    round(pg_database_size(current_database()) / 1e9, 1) as value

union all

select
    2 as section,
    sources.source as item,
    days.captured_on,
    count(captures.source) as value
from sources
cross join days
left join captures
    on sources.source = captures.source
    and days.captured_on = captures.captured_on
group by sources.source, days.captured_on

union all

{% if node_results is not none %}
select
    3 as section,
    node_id || ': ' || status as item,
    max(recorded_at)::date as captured_on,
    count(*) as value
from {{ node_results }}
where status not in ('success', 'pass')
{% if since is not none %}
    and recorded_at >= timestamptz '{{ since }}'
{% else %}
    and recorded_at >= now() - interval '7 days'
{% endif %}
group by node_id, status
{% else %}
-- No audited dbt run has created the table yet.
select 3 as section, 'dbt_node_results: not found' as item, null::date, null
{% endif %}

order by section, item, captured_on
