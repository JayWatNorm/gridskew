with duplicate_positions as (
    select extract_id, source_index
    from {{ ref('stg_elexon__bm_units') }} as staging
    group by extract_id, source_index
    having count(*) <> 1
),
raw_counts as (
    select extract_id, count(*) as rows
    from {{ source('elexon', 'elexon_bm_units') }}
    group by extract_id
),
staging_counts as (
    select extract_id, count(*) as rows
    from {{ ref('stg_elexon__bm_units') }}
    group by extract_id
),
count_mismatch as (
    select coalesce(raw.extract_id, staging.extract_id) as extract_id
    from raw_counts as raw
    full outer join staging_counts as staging using (extract_id)
    where coalesce(raw.rows, 0) <> coalesce(staging.rows, 0)
)
select extract_id from duplicate_positions
union
select extract_id from count_mismatch
