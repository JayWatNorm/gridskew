-- Warning: a stored carbon actual that cannot be right. The API's actual is
-- a weighted average of positive factors over a mix that always holds some
-- gas, biomass or imports, so zero or below is a source fault, not a reading.
-- One such value is stored: 0 g/kWh for 6 August 2026, 11:00 UTC. Every
-- capture is kept, so each poll that served the value is one row here, and
-- the warning stays while the rows do. The analyses decide what to do with
-- the half-hour; this test only names it.

{{ config(severity='warn') }}

select
    period_start,
    retrieved_at,
    actual
from {{ ref('stg_carbon_intensity__outturn') }}
where actual <= 0
