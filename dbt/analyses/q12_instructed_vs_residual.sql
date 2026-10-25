{{ config(group='analysis') }}

-- Q12: how much of the deviation from PN was instructed rather than
-- unexplained? Reading rule (research questions): describe, per fuel group,
-- the share of the deviation from PN that the acceptances in force asked
-- for. deviation_from_pn_mwh = instructed_deviation_mwh +
-- unexplained_shortfall_mwh in every determinable period with a PN, so the
-- two parts are reported as sums of absolute energy and the instructed part
-- as a share of their total. A period determinable without a PN (its
-- acceptances cover the whole half-hour) has a shortfall but no deviation to
-- split, so it is left out here and counted in q3_coverage.sql, which also
-- splits the population by metered settlement run. Instructed energy is
-- never added up across overlapping acceptances: the interval model keeps
-- the acceptance in force for each minute only. The figures come from
-- agg_delivery_by_fuel_group, which holds them up to the analysis_read_until
-- it was built with.

select
    fuel_group,
    unit_periods,
    first_date,
    last_date,
    instructed_periods,
    instructed_period_share,
    abs_deviation_mwh,
    abs_instructed_deviation_mwh,
    abs_unexplained_shortfall_mwh,
    instructed_share_of_abs_deviation
from {{ ref('agg_delivery_by_fuel_group') }}
order by abs_deviation_mwh desc
