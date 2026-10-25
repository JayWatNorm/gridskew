{{ config(group='analysis') }}

-- Q3: how far is metered output from the notified position, by fuel group?
-- Reading rule (research questions): describe only, the median and
-- interquartile range of deviation_from_pn_mwh per fuel group, with n, the
-- date range and coverage beside it. Every figure is conditional on the
-- cohort (registry units with a fuel type); q3_coverage.sql reports what the
-- cohort leaves out and the settlement runs behind the metered values. Reads
-- determinable periods with a PN: a period whose acceptances cover the whole
-- half-hour is determinable without one, and has no deviation from PN to
-- describe. The figures come from agg_delivery_by_fuel_group, which holds
-- them up to the analysis_read_until it was built with.

select
    fuel_group,
    unit_periods,
    units,
    first_date,
    last_date,
    median_deviation_mwh,
    q1_deviation_mwh,
    q3_deviation_mwh,
    abs_metered_mwh,
    abs_pn_mwh
from {{ ref('agg_delivery_by_fuel_group') }}
order by abs_metered_mwh desc
