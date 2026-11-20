-- Q2: how much does metered output change between settlement runs?
-- The query as run on 11 October 2026 against the raw B1610 table, after the
-- reading rule in docs/research-questions.md was recorded and all seven
-- settlement dates held R1. Each statement is one SELECT, run inside a
-- read-only transaction with a statement timeout:
--
--   begin transaction read only;
--   set local statement_timeout = '10min';
--   ... one query ...
--   rollback;
--
-- Outputs: statement 1 is q2_settlement_run_changes.csv; statement 2 is
-- q2_changes_by_unit_prefix.csv; statement 3, with the three gas units
-- lettered in place of their names, is q2_gas_units_revisions.csv.

-- 0. Precondition: every date holds II, SF and R1 with all 48 periods.
--    Counts only; no quantity is read. Expect 21 rows, each with 48 periods.
select
    settlement_date,
    settlement_run_type,
    count(*) as row_count,
    count(distinct settlement_period) as periods,
    count(distinct bm_unit) as units
from raw.elexon_b1610
where settlement_date between date '2026-08-10' and date '2026-08-16'
    and settlement_run_type in ('II', 'SF', 'R1')
group by settlement_date, settlement_run_type
order by settlement_date, settlement_run_type;

-- 1. Restatement per run pair, with the pre-registered labels.
--    The labels are tested on the exact shares; the rounded shares are for
--    reading only.
with cohort as (
    select bm_unit, settlement_date, settlement_period, settlement_run_type, quantity
    from raw.elexon_b1610
    where settlement_date between date '2026-08-10' and date '2026-08-16'
        and settlement_run_type in ('II', 'SF', 'R1')
),

ii as (select * from cohort where settlement_run_type = 'II'),

sf as (select * from cohort where settlement_run_type = 'SF'),

r1 as (select * from cohort where settlement_run_type = 'R1'),

-- Full joins: a unit-period present in only one run of a pair is counted,
-- not hidden.
pairs as (
    select
        'II->SF' as run_pair,
        ii.quantity as earlier_mwh,
        sf.quantity as later_mwh
    from ii
    full join sf using (bm_unit, settlement_date, settlement_period)
    union all
    select
        'SF->R1' as run_pair,
        sf.quantity as earlier_mwh,
        r1.quantity as later_mwh
    from sf
    full join r1 using (bm_unit, settlement_date, settlement_period)
),

totals as (
    select
        run_pair,
        count(*) filter (
            where earlier_mwh is not null and later_mwh is not null
        ) as matched,
        count(*) filter (where earlier_mwh is null) as only_later,
        count(*) filter (where later_mwh is null) as only_earlier,
        count(*) filter (where earlier_mwh <> later_mwh) as changed,
        sum(abs(later_mwh - earlier_mwh)) as moved_mwh,
        sum(abs(earlier_mwh)) filter (where later_mwh is not null) as base_abs_mwh
    from pairs
    group by run_pair
),

shares as (
    select
        *,
        changed::numeric / nullif(matched, 0) as changed_share,
        moved_mwh / nullif(base_abs_mwh, 0) as moved_share
    from totals
),

labels as (
    select
        *,
        case
            when moved_share < 0.005 then 'small'
            when moved_share >= 0.02 then 'material'
            else 'intermediate'
        end as moved_label,
        case
            when changed_share < 0.05 then 'small'
            when changed_share >= 0.20 then 'material'
            else 'intermediate'
        end as changed_label
    from shares
)

select
    run_pair,
    matched,
    only_later,
    only_earlier,
    changed,
    moved_mwh,
    base_abs_mwh,
    round(changed_share, 6) as changed_share,
    round(moved_share, 6) as moved_share,
    changed_label,
    moved_label,
    case
        when moved_label = 'material' or changed_label = 'material' then 'material'
        when moved_label = 'small' and changed_label = 'small' then 'small'
        else 'intermediate'
    end as pair_label
from labels
order by run_pair;

-- 2. Where the SF -> R1 movement is, by BM-unit prefix
--    (T transmission, E embedded, 2 supplier, I interconnector, ...).
with cohort as (
    select bm_unit, settlement_date, settlement_period, settlement_run_type, quantity
    from raw.elexon_b1610
    where settlement_date between date '2026-08-10' and date '2026-08-16'
        and settlement_run_type in ('SF', 'R1')
)

select
    split_part(sf.bm_unit, '_', 1) as unit_prefix,
    count(*) as matched,
    count(*) filter (where sf.quantity <> r1.quantity) as changed,
    sum(abs(r1.quantity - sf.quantity)) as moved_mwh
from cohort as sf
inner join cohort as r1 using (bm_unit, settlement_date, settlement_period)
where sf.settlement_run_type = 'SF'
    and r1.settlement_run_type = 'R1'
group by split_part(sf.bm_unit, '_', 1)
order by moved_mwh desc;

-- 3. The 20 units with the most SF -> R1 movement.
with cohort as (
    select bm_unit, settlement_date, settlement_period, settlement_run_type, quantity
    from raw.elexon_b1610
    where settlement_date between date '2026-08-10' and date '2026-08-16'
        and settlement_run_type in ('SF', 'R1')
)

select
    sf.bm_unit,
    sum(abs(r1.quantity - sf.quantity)) as moved_mwh,
    sum(r1.quantity - sf.quantity) as net_change_mwh,
    sum(abs(sf.quantity)) as sf_abs_mwh
from cohort as sf
inner join cohort as r1 using (bm_unit, settlement_date, settlement_period)
where sf.settlement_run_type = 'SF'
    and r1.settlement_run_type = 'R1'
group by sf.bm_unit
order by moved_mwh desc
limit 20;
