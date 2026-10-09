-- How much of the period-table input is zero, and does the zero carry information?
--
-- Read-only. Run as gridskew_dbt against gridskew_prod with psql:
--   psql -X -v ON_ERROR_STOP=1 -f tests/adhoc/s6_zero_volume_checks.sql
-- Temporary tables are session-local.
--
--   Z1  Zero share of B1610 rows (full history), and units that are always zero.
--   Z2  Zero share of PN segments (full history).
--   Z3  August 2026 at unit-period grain: PN zero or not against metered zero
--       or not, for units in both sources (the shortfall population).
--   Z4  August 2026: PN zero periods next to a non-zero period (ramp edges).
--   Z5  B1610 zero rows with and without a National Grid ID.

\timing on
set statement_timeout = '20min';

-- ---------------------------------------------------------------- Z1
select
    count(*) as b1610_rows,
    count(*) filter (where quantity = 0) as zero_rows,
    round(100.0 * count(*) filter (where quantity = 0) / count(*), 1) as zero_pct
from raw.elexon_b1610;

select
    count(*) as b1610_units,
    count(*) filter (where max_abs = 0) as always_zero_units,
    count(*) filter (where max_abs = 0 and has_ng_id) as always_zero_units_with_ng_id
from (
    select bm_unit, max(abs(quantity)) as max_abs,
           bool_or(national_grid_bm_unit_id is not null) as has_ng_id
    from raw.elexon_b1610
    group by bm_unit
) as units;

-- ---------------------------------------------------------------- Z2
select
    count(*) as pn_segments,
    count(*) filter (where level_from = 0 and level_to = 0) as zero_segments,
    round(100.0 * count(*) filter (where level_from = 0 and level_to = 0) / count(*), 1)
        as zero_pct
from raw.elexon_pn;

-- ---------------------------------------------------------------- Z3
-- Period-level approximations for one month: a PN period is zero when every
-- segment of every capture is 0 -> 0; a B1610 period is zero when every run
-- reads 0. Mapping through the current registry.
create temp table z_pn as
select national_grid_bm_unit, settlement_date, settlement_period,
       max(abs(level_from) + abs(level_to)) = 0 as pn_zero
from raw.elexon_pn
where settlement_date between date '2026-08-01' and date '2026-08-31'
group by 1, 2, 3;

create temp table z_b1610 as
select bm_unit, settlement_date, settlement_period,
       max(abs(quantity)) = 0 as metered_zero
from raw.elexon_b1610
where settlement_date between date '2026-08-01' and date '2026-08-31'
group by 1, 2, 3;

create temp table z_registry as
select national_grid_bm_unit, elexon_bm_unit
from dbt_dev.int_elexon__bm_units_current
where elexon_bm_unit is not null;

select
    count(*) as pn_unit_periods,
    count(*) filter (where pn_zero) as pn_zero_periods,
    round(100.0 * count(*) filter (where pn_zero) / count(*), 1) as pn_zero_pct
from z_pn;

select
    count(*) as b1610_unit_periods,
    count(*) filter (where metered_zero) as metered_zero_periods,
    round(100.0 * count(*) filter (where metered_zero) / count(*), 1) as metered_zero_pct
from z_b1610;

with joined as (
    select
        p.pn_zero,
        b.metered_zero,
        b.bm_unit is not null as has_b1610
    from z_pn as p
    left join z_registry as r using (national_grid_bm_unit)
    left join z_b1610 as b
        on b.bm_unit = r.elexon_bm_unit
        and b.settlement_date = p.settlement_date
        and b.settlement_period = p.settlement_period
)
select
    case
        when not has_b1610 then 'PN only (no mapped B1610 period)'
        when pn_zero and metered_zero then 'both zero'
        when pn_zero then 'PN zero, metered non-zero'
        when metered_zero then 'PN non-zero, metered zero'
        else 'both non-zero'
    end as class,
    count(*) as unit_periods,
    round(100.0 * count(*) / sum(count(*)) over (), 1) as pct
from joined
group by 1
order by 2 desc;

-- ---------------------------------------------------------------- Z4
with ordered as (
    select *,
        lag(pn_zero) over w as previous_zero,
        lead(pn_zero) over w as next_zero
    from z_pn
    window w as (
        partition by national_grid_bm_unit
        order by settlement_date, settlement_period
    )
)
select
    count(*) filter (where pn_zero) as pn_zero_periods,
    count(*) filter (
        where pn_zero and (previous_zero is false or next_zero is false)
    ) as zero_next_to_non_zero,
    count(distinct national_grid_bm_unit) filter (where pn_zero) as units_with_zero,
    count(distinct national_grid_bm_unit) as units
from ordered;

-- ---------------------------------------------------------------- Z5
-- B1610 zero rows by whether the row carries a National Grid ID (units with
-- one can appear in PN; embedded units without one cannot).
select
    national_grid_bm_unit_id is not null as has_ng_id,
    count(*) as rows,
    count(*) filter (where quantity = 0) as zero_rows,
    round(100.0 * count(*) filter (where quantity = 0) / count(*), 1) as zero_pct
from raw.elexon_b1610
group by 1
order by 1;