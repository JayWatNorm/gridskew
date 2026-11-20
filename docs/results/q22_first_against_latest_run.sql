-- Q22: did later meter data change one week's fuel-group picture?
-- The query as run on 11 October 2026 against the built dbt schema, after
-- the reading rule in docs/research-questions.md was recorded (9 October
-- 2026): the Q3 statistics (median and interquartile range of the deviation
-- from PN per fuel group) on the first run and on the latest run of
-- settlement dates 10 to 16 August 2026, side by side. Same population as
-- q3_metered_vs_notified.sql: cohort join, determinable, PN present;
-- first-run deviation = pn_mwh - first_metered_mwh.
-- Output: q22_first_against_latest_run.csv.
with cohort_week as (
    select
        cohort.fuel_group,
        shortfall.first_run_code,
        shortfall.metered_run_code,
        shortfall.pn_mwh - shortfall.first_metered_mwh as first_deviation_mwh,
        shortfall.deviation_from_pn_mwh as latest_deviation_mwh
    from dbt_dev.int_shortfall_by_unit_period as shortfall
    inner join dbt_dev.int_elexon__bm_unit_cohort as cohort
        on shortfall.national_grid_bm_unit = cohort.national_grid_bm_unit
    where shortfall.settlement_date between date '2026-08-10' and date '2026-08-16'
        and shortfall.determinability = 'determinable'
        and shortfall.pn_mwh is not null
        and shortfall.first_metered_mwh is not null
)

select
    fuel_group,
    count(*) as unit_periods,
    string_agg(distinct first_run_code, ',') as first_runs,
    string_agg(distinct metered_run_code, ',') as latest_runs,
    round(percentile_cont(0.25) within group (order by first_deviation_mwh)::numeric, 3) as first_q1,
    round(percentile_cont(0.5) within group (order by first_deviation_mwh)::numeric, 3) as first_median,
    round(percentile_cont(0.75) within group (order by first_deviation_mwh)::numeric, 3) as first_q3,
    round(percentile_cont(0.25) within group (order by latest_deviation_mwh)::numeric, 3) as latest_q1,
    round(percentile_cont(0.5) within group (order by latest_deviation_mwh)::numeric, 3) as latest_median,
    round(percentile_cont(0.75) within group (order by latest_deviation_mwh)::numeric, 3) as latest_q3,
    percentile_cont(0.5) within group (order by first_deviation_mwh) as first_median_exact,
    percentile_cont(0.5) within group (order by latest_deviation_mwh) as latest_median_exact
from cohort_week
group by fuel_group
order by fuel_group
