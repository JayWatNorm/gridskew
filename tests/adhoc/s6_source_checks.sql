-- Source checks for the period tables.
--
-- Read-only. Run as gridskew_dbt against gridskew_prod with psql:
--   psql -X -v ON_ERROR_STOP=1 -f tests/adhoc/s6_source_checks.sql
-- Temporary tables are session-local and disappear on disconnect. The script
-- writes nothing else; gridskew_dbt has only SELECT on raw.
--
-- Questions:
--   B1  Does every B1610 run code exist in the settlement-run seed?
--   B2  Within the bounded cohort, is a later run ever captured before an
--       earlier one (the case "first captured" must handle)?
--   R1  Is the current registry unique on each identifier used for mapping?
--   R2  Does the registry observation time change on every poll, and do
--       attributes change between the two latest extracts?
--   P1  How many PN captures exist per unit-period?
--   P2  Does the latest capture cover each period exactly once? Profile
--       segments outside the declared period, UTC request edges and clock
--       changes, and show representative rows.
--   P3  Does the echo rule (ignore a capture that only repeats earlier
--       segments) remove the edge incompletes?
--   M2  How do PN National Grid IDs map to the current registry?

\timing on
set statement_timeout = '15min';

-- ---------------------------------------------------------------- B1
select
    b.settlement_run_type,
    s.settlement_run_code is not null as in_seed,
    count(*) as row_count,
    min(b.settlement_date) as first_date,
    max(b.settlement_date) as last_date
from raw.elexon_b1610 as b
left join dbt_dev.elexon_settlement_run_codes as s
    on b.settlement_run_type = s.settlement_run_code
group by 1, 2
order by 1;

-- ---------------------------------------------------------------- B2
create temp table s6_b1610_cohort as
select bm_unit, national_grid_bm_unit_id, settlement_date, settlement_period,
       settlement_run_type, quantity, retrieved_at
from raw.elexon_b1610
where settlement_date between date '2026-08-10' and date '2026-08-16';

select
    count(*) as keys_with_ii_and_sf,
    count(*) filter (where sf.retrieved_at < ii.retrieved_at) as sf_captured_before_ii,
    count(*) filter (where sf.retrieved_at = ii.retrieved_at) as same_capture_time,
    count(*) filter (where sf.quantity <> ii.quantity) as sf_revised_value
from s6_b1610_cohort as ii
inner join s6_b1610_cohort as sf
    on ii.bm_unit = sf.bm_unit
    and ii.settlement_date = sf.settlement_date
    and ii.settlement_period = sf.settlement_period
where ii.settlement_run_type = 'II'
    and sf.settlement_run_type = 'SF';

-- ---------------------------------------------------------------- R1
select
    count(*) as registry_rows,
    count(distinct national_grid_bm_unit) as distinct_ng_ids,
    count(elexon_bm_unit) as rows_with_elexon_id,
    count(distinct elexon_bm_unit) as distinct_elexon_ids
from dbt_dev.int_elexon__bm_units_current;

-- Elexon IDs shared by more than one National Grid ID (B1610 ambiguity).
select elexon_bm_unit, array_agg(national_grid_bm_unit order by national_grid_bm_unit) as ng_ids
from dbt_dev.int_elexon__bm_units_current
where elexon_bm_unit is not null
group by elexon_bm_unit
having count(*) > 1
order by elexon_bm_unit
limit 20;

-- B1610 cohort mapping status by distinct unit (bounded).
with units as (
    select distinct bm_unit, national_grid_bm_unit_id
    from s6_b1610_cohort
),
candidates as (
    select
        u.bm_unit,
        u.national_grid_bm_unit_id,
        count(r.national_grid_bm_unit) as candidate_count,
        min(r.national_grid_bm_unit) as resolved_ng
    from units as u
    left join dbt_dev.int_elexon__bm_units_current as r
        on u.bm_unit = r.elexon_bm_unit
    group by 1, 2
)
select
    case
        when candidate_count = 0 then 'unmapped'
        when candidate_count > 1 then 'ambiguous'
        when national_grid_bm_unit_id is not null
            and national_grid_bm_unit_id <> resolved_ng then 'conflict'
        else 'mapped'
    end as mapping_status,
    count(*) as unit_count
from candidates
group by 1
order by 1;

-- Does one B1610 bm_unit carry more than one raw NG ID in the cohort?
select count(*) as bm_units_with_multiple_raw_ng_ids
from (
    select bm_unit
    from s6_b1610_cohort
    group by bm_unit
    having count(distinct national_grid_bm_unit_id) > 1
) as multi;

-- ---------------------------------------------------------------- R2
select extract_id, retrieved_at, row_count, unit_count
from raw.elexon_bm_units_extracts
order by retrieved_at desc, extract_id desc
limit 5;

with ranked as (
    select extract_id, row_number() over (order by retrieved_at desc, extract_id desc) as rn
    from raw.elexon_bm_units_extracts
),
latest as (
    select distinct b.national_grid_bm_unit, b.elexon_bm_unit, b.fuel_type, b.bm_unit_type
    from raw.elexon_bm_units as b
    inner join ranked on b.extract_id = ranked.extract_id and ranked.rn = 1
),
previous as (
    select distinct b.national_grid_bm_unit, b.elexon_bm_unit, b.fuel_type, b.bm_unit_type
    from raw.elexon_bm_units as b
    inner join ranked on b.extract_id = ranked.extract_id and ranked.rn = 2
)
select
    (select count(*) from (select * from latest except select * from previous) as x) as rows_new_or_changed,
    (select count(*) from (select * from previous except select * from latest) as x) as rows_removed_or_changed;

-- ---------------------------------------------------------------- P1
-- Bounded PN sample: first backfill day, both clock changes, and the
-- latest complete live week.
create temp table s6_pn_sample as
select
    national_grid_bm_unit,
    bm_unit,
    settlement_date,
    settlement_period,
    (settlement_date::timestamp at time zone 'Europe/London')
        + (settlement_period - 1) * interval '30 minutes' as period_start_utc,
    time_from,
    time_to,
    level_from,
    level_to,
    retrieved_at
from raw.elexon_pn
where settlement_date in (date '2025-08-22', date '2025-10-26', date '2026-03-29')
    or settlement_date between date '2026-09-14' and date '2026-09-20';

select
    min(settlement_date) as first_date,
    max(settlement_date) as last_date,
    count(distinct settlement_date) as dates,
    count(*) as segment_rows,
    count(distinct (national_grid_bm_unit, settlement_date, settlement_period)) as unit_periods
from s6_pn_sample;

with per_period as (
    select national_grid_bm_unit, settlement_date, settlement_period,
           count(distinct retrieved_at) as captures
    from s6_pn_sample
    group by 1, 2, 3
)
select captures, count(*) as unit_periods
from per_period
group by 1
order by 1;

-- ---------------------------------------------------------------- P2
create temp table s6_pn_latest as
with latest_capture as (
    select national_grid_bm_unit, settlement_date, settlement_period,
           max(retrieved_at) as retrieved_at
    from s6_pn_sample
    group by 1, 2, 3
)
select s.*
from s6_pn_sample as s
inner join latest_capture as l
    using (national_grid_bm_unit, settlement_date, settlement_period, retrieved_at);

create temp table s6_pn_coverage as
with ordered as (
    select
        *,
        lag(time_to) over (
            partition by national_grid_bm_unit, settlement_date, settlement_period
            order by time_from
        ) as previous_time_to
    from s6_pn_latest
)
select
    national_grid_bm_unit,
    settlement_date,
    settlement_period,
    period_start_utc,
    retrieved_at,
    count(*) as segments,
    min(time_from) = period_start_utc
        and max(time_to) = period_start_utc + interval '30 minutes' as spans_period,
    count(*) filter (where time_to <= time_from) as non_positive_segments,
    count(*) filter (where previous_time_to < time_from) as gap_count,
    count(*) filter (where previous_time_to > time_from) as overlap_count,
    count(*) filter (
        where time_from < period_start_utc
            or time_to > period_start_utc + interval '30 minutes'
    ) as outside_period
from ordered
group by 1, 2, 3, 4, 5;

select
    case
        when outside_period > 0 or non_positive_segments > 0 then 'invalid'
        when gap_count > 0 or overlap_count > 0 or not spans_period then 'incomplete'
        else 'complete'
    end as coverage_status,
    (period_start_utc at time zone 'UTC')::time in ('00:00', '23:30') as at_utc_request_edge,
    settlement_date in (date '2025-10-26', date '2026-03-29') as clock_change_date,
    count(*) as unit_periods,
    sum(outside_period) as outside_period_segments
from s6_pn_coverage
group by 1, 2, 3
order by 1, 2, 3;

-- Periods per date (46/50 expected on clock changes).
select settlement_date, count(distinct settlement_period) as periods,
       min(settlement_period) as first_period, max(settlement_period) as last_period
from s6_pn_sample
group by 1
order by 1;

-- Where the latest capture is not complete, does an older capture of the same
-- unit-period cover it? (Evidence for "do not fall back to an older capture".)
select
    count(*) as not_complete_latest,
    count(*) filter (where older.retrieved_at is not null) as has_older_capture
from s6_pn_coverage as c
left join lateral (
    select max(s.retrieved_at) as retrieved_at
    from s6_pn_sample as s
    where s.national_grid_bm_unit = c.national_grid_bm_unit
        and s.settlement_date = c.settlement_date
        and s.settlement_period = c.settlement_period
        and s.retrieved_at < c.retrieved_at
) as older on true
where not (c.spans_period and c.gap_count = 0 and c.overlap_count = 0
           and c.outside_period = 0 and c.non_positive_segments = 0);

-- Representative rows: segments outside the declared period, then
-- incomplete periods, with UTC endpoints.
(
    select 'outside_period' as reason, s.national_grid_bm_unit, s.settlement_date,
           s.settlement_period, s.period_start_utc, s.time_from, s.time_to,
           s.level_from, s.level_to, s.retrieved_at
    from s6_pn_latest as s
    where s.time_from < s.period_start_utc
        or s.time_to > s.period_start_utc + interval '30 minutes'
    order by s.settlement_date, s.settlement_period, s.national_grid_bm_unit, s.time_from
    limit 10
)
union all
(
    select 'incomplete' as reason, s.national_grid_bm_unit, s.settlement_date,
           s.settlement_period, s.period_start_utc, s.time_from, s.time_to,
           s.level_from, s.level_to, s.retrieved_at
    from s6_pn_latest as s
    inner join s6_pn_coverage as c
        using (national_grid_bm_unit, settlement_date, settlement_period)
    where c.outside_period = 0
        and (c.gap_count > 0 or c.overlap_count > 0 or not c.spans_period)
    order by s.settlement_date, s.settlement_period, s.national_grid_bm_unit, s.time_from
    limit 10
);

-- ---------------------------------------------------------------- P3
-- Echo rule (James, 26 September 2026): a later capture whose segments all
-- exactly repeat segments of an earlier capture of the same unit-period is
-- ignored; the latest remaining capture is used. Compare coverage under the
-- original rule (latest capture) and the echo rule.
create temp table s6_pn_captures as
with marked as (
    select
        *,
        count(*) over (
            partition by national_grid_bm_unit, settlement_date, settlement_period,
                time_from, time_to, level_from, level_to
            order by retrieved_at
        ) > 1 as repeats_earlier
    from s6_pn_sample
)
select
    national_grid_bm_unit,
    settlement_date,
    settlement_period,
    period_start_utc,
    retrieved_at,
    bool_and(repeats_earlier) as is_echo,
    count(*) as segments,
    min(time_from) = period_start_utc
        and max(time_to) = period_start_utc + interval '30 minutes'
        and sum(extract(epoch from time_to - time_from)) = 1800
        and bool_and(time_to > time_from) as covers_period
from marked
group by 1, 2, 3, 4, 5;

with ranked as (
    select
        *,
        row_number() over (
            partition by national_grid_bm_unit, settlement_date, settlement_period
            order by retrieved_at desc
        ) as latest_rank,
        row_number() over (
            partition by national_grid_bm_unit, settlement_date, settlement_period
            order by is_echo, retrieved_at desc
        ) as echo_rule_rank
    from s6_pn_captures
)
select
    'latest capture' as rule,
    covers_period,
    count(*) as unit_periods
from ranked where latest_rank = 1 group by 1, 2
union all
select
    'echo rule' as rule,
    covers_period,
    count(*) as unit_periods
from ranked where echo_rule_rank = 1 group by 1, 2
order by 1, 2;

-- Echo captures and non-echo repeat captures that differ from the one before.
select
    count(*) filter (where is_echo) as echo_captures,
    count(*) filter (where not is_echo and retrieved_at > first_capture) as later_non_echo_captures
from (
    select *, min(retrieved_at) over (
        partition by national_grid_bm_unit, settlement_date, settlement_period
    ) as first_capture
    from s6_pn_captures
) as c;

-- Incomplete under the echo rule, first 10.
with chosen as (
    select distinct on (national_grid_bm_unit, settlement_date, settlement_period) *
    from s6_pn_captures
    order by national_grid_bm_unit, settlement_date, settlement_period,
        is_echo, retrieved_at desc
)
select national_grid_bm_unit, settlement_date, settlement_period, retrieved_at, segments
from chosen
where not covers_period
order by settlement_date, settlement_period, national_grid_bm_unit
limit 10;

-- ---------------------------------------------------------------- M2
-- PN mapping against the current registry (National Grid ID is the registry
-- key, so a match is unique by the registry's unique test).
with units as (
    select distinct national_grid_bm_unit, bm_unit from s6_pn_sample
)
select
    case
        when r.national_grid_bm_unit is null then 'unmapped'
        when u.bm_unit is not null and u.bm_unit is distinct from r.elexon_bm_unit
            then 'conflict'
        else 'mapped'
    end as mapping_status,
    count(*) as unit_count
from units as u
left join dbt_dev.int_elexon__bm_units_current as r
    on u.national_grid_bm_unit = r.national_grid_bm_unit
group by 1
order by 1;

-- Does one PN National Grid ID carry more than one Elexon ID in the sample?
select count(*) as ng_units_with_multiple_elexon_ids
from (
    select national_grid_bm_unit
    from s6_pn_sample
    group by 1
    having count(distinct bm_unit) > 1
) as multi;
