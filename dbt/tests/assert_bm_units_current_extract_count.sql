with latest as (
    select extract_id, row_count, unit_count
    from {{ source('elexon', 'elexon_bm_units_extracts') }}
    order by retrieved_at desc, extract_id desc
    limit 1
),
actual as (
    select
        count(*) as raw_rows,
        count(distinct national_grid_bm_unit) as raw_units
    from {{ source('elexon', 'elexon_bm_units') }}
    where extract_id = (select extract_id from latest)
),
current_state as (
    select
        count(*) as current_rows,
        coalesce(sum(source_row_count), 0) as source_rows
    from {{ ref('int_elexon__bm_units_current') }}
)
select latest.extract_id
from actual
cross join current_state
left join latest on true
where latest.extract_id is null
   or latest.row_count <> actual.raw_rows
   or latest.unit_count <> actual.raw_units
   or latest.unit_count <> current_state.current_rows
   or latest.row_count <> current_state.source_rows
