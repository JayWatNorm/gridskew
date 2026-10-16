{{ config(group='analysis') }}

-- Does NESO resubmit QAS values that reach Insights, and can a later capture
-- leave a unit out? GS-33 relies on its 35- and 450-day captures only once
-- this has been read. Counts only, no energy value.
--
-- For each settlement date captured more than once, each capture is compared
-- with the capture before it: the unit-periods in either, how many changed
-- value, how many are new and how many are dropped (in the earlier capture,
-- not the later). int_shortfall_by_unit_period keeps a dropped unit-period's
-- earlier value; a dropped count above 0 asks James how a dropped unit should
-- read before Q26 is read. Grouped by the later capture's age in days (UTC
-- capture date minus settlement date), with the earlier capture's ages.

with captures as (
    select
        settlement_date,
        retrieved_at,
        row_number() over (
            partition by settlement_date order by retrieved_at
        ) as capture_number
    from (
        select distinct settlement_date, retrieved_at
        from {{ ref('stg_elexon__qas') }}
    ) as distinct_captures
),

pairs as (
    select
        later.settlement_date,
        earlier.retrieved_at as earlier_retrieved_at,
        later.retrieved_at as later_retrieved_at
    from captures as later
    inner join captures as earlier
        on later.settlement_date = earlier.settlement_date
        and later.capture_number = earlier.capture_number + 1
),

earlier_rows as (
    select
        pairs.settlement_date,
        pairs.earlier_retrieved_at,
        pairs.later_retrieved_at,
        qas.national_grid_bm_unit,
        qas.settlement_period,
        qas.qas_mwh
    from pairs
    inner join {{ ref('stg_elexon__qas') }} as qas
        on qas.settlement_date = pairs.settlement_date
        and qas.retrieved_at = pairs.earlier_retrieved_at
),

later_rows as (
    select
        pairs.settlement_date,
        pairs.earlier_retrieved_at,
        pairs.later_retrieved_at,
        qas.national_grid_bm_unit,
        qas.settlement_period,
        qas.qas_mwh
    from pairs
    inner join {{ ref('stg_elexon__qas') }} as qas
        on qas.settlement_date = pairs.settlement_date
        and qas.retrieved_at = pairs.later_retrieved_at
),

compared as (
    select
        coalesce(earlier_rows.settlement_date, later_rows.settlement_date)
            as settlement_date,
        coalesce(earlier_rows.earlier_retrieved_at, later_rows.earlier_retrieved_at)
            as earlier_retrieved_at,
        coalesce(earlier_rows.later_retrieved_at, later_rows.later_retrieved_at)
            as later_retrieved_at,
        earlier_rows.qas_mwh as earlier_mwh,
        later_rows.qas_mwh as later_mwh
    from earlier_rows
    full join later_rows
        on earlier_rows.settlement_date = later_rows.settlement_date
        and earlier_rows.later_retrieved_at = later_rows.later_retrieved_at
        and earlier_rows.national_grid_bm_unit = later_rows.national_grid_bm_unit
        and earlier_rows.settlement_period = later_rows.settlement_period
)

select
    (later_retrieved_at at time zone 'UTC')::date - settlement_date
        as later_capture_age_days,
    min((earlier_retrieved_at at time zone 'UTC')::date - settlement_date)
        as earliest_earlier_capture_age_days,
    max((earlier_retrieved_at at time zone 'UTC')::date - settlement_date)
        as latest_earlier_capture_age_days,
    count(distinct settlement_date) as settlement_dates,
    count(*) as unit_periods,
    count(*) filter (
        where earlier_mwh is not null
            and later_mwh is not null
            and earlier_mwh <> later_mwh
    ) as changed,
    count(*) filter (where earlier_mwh is null) as new_in_later_capture,
    count(*) filter (where later_mwh is null) as dropped_from_later_capture
from compared
group by 1
order by 1
