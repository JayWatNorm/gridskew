-- Warning: a stored carbon actual of zero or below. The API's own method is
-- not documented as able to give one, so the test reports it for a decision.
-- As of 20 November 2026 the outturn table holds 48,861 rows over 21,951
-- half-hours; one row, 0 g/kWh for 6 August 2026 11:00 UTC, has an actual of
-- zero or below. That is one half-hour, which may appear in several rows
-- because every capture is kept (here it is one row); the warning stays
-- while the rows do. The analyses decide what to do with the half-hour;
-- this test only names it.

{{ config(severity='warn') }}

select
    period_start,
    retrieved_at,
    actual
from {{ ref('stg_carbon_intensity__outturn') }}
where actual <= 0
