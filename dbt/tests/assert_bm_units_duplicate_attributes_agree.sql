with latest as (
    select extract_id
    from {{ source('elexon', 'elexon_bm_units_extracts') }}
    order by retrieved_at desc, extract_id desc
    limit 1
)
select national_grid_bm_unit
from {{ source('elexon', 'elexon_bm_units') }} as source_rows
where extract_id = (select extract_id from latest)
group by national_grid_bm_unit
having count(distinct to_jsonb(source_rows) - 'extract_id' - 'source_index' - 'eic') > 1
