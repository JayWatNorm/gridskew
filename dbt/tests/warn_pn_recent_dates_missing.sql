-- Warning: a settlement date in the last 30 days with no PN row. Freshness
-- cannot see a missing day, because the next day's capture keeps the source
-- fresh. The capture retries for about four days, so a date listed here has
-- given up and needs its task cleared. The last two days are left out: they
-- may not be captured yet. Reads only rows captured in the last 31 days,
-- through the retrieved_at index.

{{ config(group='elexon_facts', severity='warn') }}

with recent_dates as (
    select generate_series(
        current_date - 30, current_date - 2, interval '1 day'
    )::date as settlement_date
),

captured_dates as (
    select distinct settlement_date
    from {{ ref('stg_elexon__pn') }}
    where retrieved_at > current_date - 31
)

select recent_dates.settlement_date
from recent_dates
left join captured_dates
    on recent_dates.settlement_date = captured_dates.settlement_date
where captured_dates.settlement_date is null
