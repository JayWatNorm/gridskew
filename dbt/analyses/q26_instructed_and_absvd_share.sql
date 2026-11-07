{{ config(group='analysis') }}

-- Q26: how much of the deviation from PN do the system operator's balancing
-- actions explain once services settled outside the balancing mechanism
-- (ABSVD) are counted? Reading rule: research questions, Q26 (recorded 12
-- October 2026, before any ABSVD capture). Per fuel group, over
-- determinable cohort periods with a PN whose settlement date has been
-- captured (absvd_mwh not null):
-- (sum|instructed_deviation| + sum|absvd|) / (sum|instructed_deviation| +
-- sum|absvd| + sum|unexplained_after_absvd|). It is a share of the three
-- components' sizes, not of each period's own deviation (signed parts can
-- offset within a period), as Q12. Beside it: n, the three sums, the date
-- range, the share of periods with any ABSVD, the periods left out because
-- their settlement date was never captured, and Q12's share over the same
-- periods for comparison. A group with no included period, or whose three
-- sums are all zero, reports the share as N/A with its n. ABSVD is the
-- latest value captured, not called final. Run once, by hand, up to
-- analysis_read_until; without it the analysis does not compile.
--
-- Amended 31 October 2026, before any read. The registered share is computed
-- as before; these apply to it and are added beside it:
-- - Capture age: a settlement date counts, for the share, the fall and the
--   placebo, when its latest ABSVD capture was made on a UTC date at least
--   35 days after it. The dates left out and their unit-periods are counted
--   (section 5). Each unit-period's ABSVD comes from the latest capture that
--   holds it, not called final.
-- - Units: only active cohort units count (active_cohort_units): those with a
--   PN or metered output above zero in at least one half-hour of the window,
--   from the start of the first counted settlement date to
--   analysis_read_until. Interconnectors (fuel type beginning INT, so INTELE
--   by assumption) are left out. The units by status, the unit-periods left
--   out, and the idle share (PN 0 and metered 0) of the result's unit-periods
--   are reported (section 6).
-- - The fall in unexplained energy, beside the share, per fuel group: U - U'
--   in MWh and (U - U') / U, where U and U' are sum|unexplained| before and
--   after ABSVD. Not called pre-registered: an exploration on 11 October 2026
--   had computed it on four days (section 2).
-- - By calendar month of the settlement date, per fuel group: the share, the
--   fall and their sums, informed by that exploration's difference between
--   winter and summer, and labelled so (section 3).
-- - Placebo: the share and the fall with each unit-period's ABSVD replaced by
--   the same unit's ABSVD for the same settlement period on the previous
--   settlement date (0 where that unit has none there). Unit-periods whose
--   previous date has no such period (periods 49 and 50 of a long day,
--   47 and 48 of the day after a short day), was not captured, or fails the
--   capture-age bar are left out and counted; the real figures are reported
--   on the placebo's unit-periods, per fuel group and overall. No test: a
--   placebo fall near the real fall says ABSVD's apparent effect is not
--   specific to the half-hour it belongs to (section 4).

{% set read_until = analysis_read_until() %}
{% if execute and read_until is none %}
    {{ exceptions.raise_compiler_error(
        "Q26 is read once at a fixed cutoff: set analysis_read_until"
    ) }}
{% endif %}
{% set minimum_capture_age_days = 35 %}
{% set all_fuel_groups = 'all fuel groups' %}
{% set not_applicable = 'N/A' %}
{% set fall_label = 'not called pre-registered (the exploration of 11 October 2026)' %}
{% set month_label = 'informed by the exploration of 11 October 2026 (falls in winter, small rises in summer)' %}
{% set placebo_detail = "no test: a placebo fall near the real fall says ABSVD''s apparent effect is not specific to the half-hour it belongs to" %}
{% set young_capture = 'less than ' ~ minimum_capture_age_days ~ ' days after' %}

-- Each captured settlement date up to the cutoff, with the age of its latest
-- capture: the UTC date of retrieved_at minus the settlement date.
with captured_dates as (
    select
        settlement_date,
        (max(retrieved_at) at time zone 'UTC')::date - settlement_date as latest_capture_age_days
    from {{ ref('stg_elexon__qas') }}
    where settlement_date <= (timestamptz '{{ read_until }}' at time zone 'Europe/London')::date
    group by settlement_date
),

dated_captures as (
    select
        settlement_date,
        latest_capture_age_days >= {{ minimum_capture_age_days }} as is_old_enough
    from captured_dates
),

counted_dates as (
    select settlement_date
    from dated_captures
    where is_old_enough
),

-- Every cohort unit with its status from the start of the first counted
-- settlement date to the cutoff.
cohort_units as (
    select
        national_grid_bm_unit,
        fuel_type,
        fuel_group,
        cohort_status
    from {{ active_cohort_units(
        "((select min(settlement_date) from counted_dates)::timestamp at time zone 'Europe/London')",
        "timestamptz '" ~ read_until ~ "'"
    ) }} as units
),

-- Every determinable cohort unit-period with a PN up to the cutoff, with the
-- reason it is left out of the result, if any. The reasons are taken in
-- this order.
unit_periods as (
    select
        units.fuel_group,
        shortfall.national_grid_bm_unit,
        shortfall.settlement_date,
        shortfall.settlement_period,
        shortfall.pn_mwh,
        shortfall.metered_mwh,
        shortfall.instructed_deviation_mwh,
        shortfall.unexplained_shortfall_mwh,
        shortfall.absvd_mwh,
        shortfall.unexplained_after_absvd_mwh,
        case
            when units.cohort_status = 'interconnector' then 'interconnector unit'
            when units.cohort_status <> 'active' then 'inactive unit'
            when shortfall.absvd_mwh is null then 'settlement date not captured'
            when not dated_captures.is_old_enough then 'capture too young'
            else 'in the result'
        end as status
    from {{ ref('int_shortfall_by_unit_period') }} as shortfall
    inner join cohort_units as units
        on shortfall.national_grid_bm_unit = units.national_grid_bm_unit
    left join dated_captures
        on shortfall.settlement_date = dated_captures.settlement_date
    where shortfall.determinability = 'determinable'
        and shortfall.pn_mwh is not null
        and coalesce(shortfall.period_start_utc, shortfall.settlement_date::timestamp at time zone 'Europe/London')
            <= timestamptz '{{ read_until }}'
),

result_unit_periods as (
    select
        fuel_group,
        national_grid_bm_unit,
        settlement_date,
        settlement_period,
        pn_mwh,
        metered_mwh,
        instructed_deviation_mwh,
        unexplained_shortfall_mwh,
        absvd_mwh,
        unexplained_after_absvd_mwh
    from unit_periods
    where status = 'in the result'
),

-- The fuel groups with an active unit's unit-period, each listed in every
-- per-group section whether or not any of its unit-periods is in the result.
fuel_groups as (
    select distinct fuel_group
    from unit_periods
    where status not in ('interconnector unit', 'inactive unit')
),

-- The same unit's ABSVD for the same settlement period on the previous
-- settlement date, from the latest capture that holds it. The same rule as
-- int_shortfall_by_unit_period's qas_latest; change both together.
previous_absvd as (
    select distinct on (
        qas.national_grid_bm_unit, qas.settlement_date, qas.settlement_period
    )
        qas.national_grid_bm_unit,
        qas.settlement_date,
        qas.settlement_period,
        qas.qas_mwh
    from {{ ref('stg_elexon__qas') }} as qas
    inner join result_unit_periods as result
        on qas.national_grid_bm_unit = result.national_grid_bm_unit
        and qas.settlement_date = result.settlement_date - 1
        and qas.settlement_period = result.settlement_period
    order by
        qas.national_grid_bm_unit,
        qas.settlement_date,
        qas.settlement_period,
        qas.retrieved_at desc
),

-- The previous settlement date holds this period only if it has at least
-- this many: 46 on a short day, 50 on a long day, 48 otherwise.
placebo_unit_periods as (
    select
        result.fuel_group,
        result.settlement_date,
        result.instructed_deviation_mwh,
        result.unexplained_shortfall_mwh,
        result.absvd_mwh,
        result.unexplained_after_absvd_mwh,
        round(coalesce(previous_absvd.qas_mwh, 0), 6) as placebo_absvd_mwh,
        round(result.unexplained_shortfall_mwh + coalesce(previous_absvd.qas_mwh, 0), 6)
            as placebo_unexplained_after_absvd_mwh,
        case
            when result.settlement_period > extract(epoch from
                (result.settlement_date::timestamp at time zone 'Europe/London')
                - ((result.settlement_date - 1)::timestamp at time zone 'Europe/London')
            ) / 1800 then 'no such period'
            when previous_dates.settlement_date is null then 'previous date not captured'
            when not previous_dates.is_old_enough then 'previous capture too young'
            else 'in the placebo'
        end as placebo_status
    from result_unit_periods as result
    left join dated_captures as previous_dates
        on previous_dates.settlement_date = result.settlement_date - 1
    left join previous_absvd
        on previous_absvd.national_grid_bm_unit = result.national_grid_bm_unit
        and previous_absvd.settlement_date = result.settlement_date - 1
        and previous_absvd.settlement_period = result.settlement_period
),

-- Each figure set's unit-periods: the result per fuel group and by month,
-- and the placebo with the real figures on its unit-periods, per fuel group
-- and over all fuel groups.
figure_inputs as (
    select
        'result' as figure_set,
        fuel_group,
        null::date as settlement_month,
        settlement_date,
        instructed_deviation_mwh,
        absvd_mwh,
        unexplained_shortfall_mwh,
        unexplained_after_absvd_mwh
    from result_unit_periods

    union all

    select
        'by month',
        fuel_group,
        date_trunc('month', settlement_date)::date,
        settlement_date,
        instructed_deviation_mwh,
        absvd_mwh,
        unexplained_shortfall_mwh,
        unexplained_after_absvd_mwh
    from result_unit_periods

    union all

    select
        'placebo',
        listed.fuel_group,
        null,
        placebo.settlement_date,
        placebo.instructed_deviation_mwh,
        placebo.placebo_absvd_mwh,
        placebo.unexplained_shortfall_mwh,
        placebo.placebo_unexplained_after_absvd_mwh
    from placebo_unit_periods as placebo
    cross join lateral (values (placebo.fuel_group), ('{{ all_fuel_groups }}')) as listed (fuel_group)
    where placebo.placebo_status = 'in the placebo'

    union all

    select
        'real on the placebo''s unit-periods',
        listed.fuel_group,
        null,
        placebo.settlement_date,
        placebo.instructed_deviation_mwh,
        placebo.absvd_mwh,
        placebo.unexplained_shortfall_mwh,
        placebo.unexplained_after_absvd_mwh
    from placebo_unit_periods as placebo
    cross join lateral (values (placebo.fuel_group), ('{{ all_fuel_groups }}')) as listed (fuel_group)
    where placebo.placebo_status = 'in the placebo'
),

figure_sums as (
    select
        figure_set,
        fuel_group,
        settlement_month,
        count(*) as unit_periods,
        count(*) filter (where absvd_mwh <> 0) as unit_periods_with_absvd,
        min(settlement_date) as first_date,
        max(settlement_date) as last_date,
        sum(abs(instructed_deviation_mwh)) as abs_instructed,
        sum(abs(absvd_mwh)) as abs_absvd,
        sum(abs(unexplained_shortfall_mwh)) as abs_unexplained_before,
        sum(abs(unexplained_after_absvd_mwh)) as abs_unexplained_after
    from figure_inputs
    group by figure_set, fuel_group, settlement_month
),

-- The rows each figure set lists: every fuel group (and, for the placebo,
-- all fuel groups) whether or not it has unit-periods; by month, only the
-- months with unit-periods.
listed_figures as (
    select
        listed.figure_set,
        fuel_groups.fuel_group,
        null::date as settlement_month
    from fuel_groups
    cross join (
        values ('result'), ('placebo'), ('real on the placebo''s unit-periods')
    ) as listed (figure_set)

    union all

    select listed.figure_set, '{{ all_fuel_groups }}', null
    from (
        values ('placebo'), ('real on the placebo''s unit-periods')
    ) as listed (figure_set)

    union all

    select figure_set, fuel_group, settlement_month
    from figure_sums
    where figure_set = 'by month'
),

-- The share, Q12's share and the fall from exact sums, rounded only here.
-- A sum over no unit-period is 0.
figures as (
    select
        listed.figure_set,
        listed.fuel_group,
        listed.settlement_month,
        coalesce(sums.unit_periods, 0) as unit_periods,
        coalesce(sums.unit_periods_with_absvd, 0) as unit_periods_with_absvd,
        sums.first_date,
        sums.last_date,
        coalesce(sums.abs_instructed, 0) as abs_instructed,
        coalesce(sums.abs_absvd, 0) as abs_absvd,
        coalesce(sums.abs_unexplained_before, 0) as abs_unexplained_before,
        coalesce(sums.abs_unexplained_after, 0) as abs_unexplained_after
    from listed_figures as listed
    left join figure_sums as sums
        on listed.figure_set = sums.figure_set
        and listed.fuel_group = sums.fuel_group
        and listed.settlement_month is not distinct from sums.settlement_month
),

measured_figures as (
    select
        figure_set,
        fuel_group,
        settlement_month,
        unit_periods,
        unit_periods_with_absvd,
        first_date,
        last_date,
        abs_instructed,
        abs_absvd,
        abs_unexplained_before,
        abs_unexplained_after,
        round(
            (abs_instructed + abs_absvd)
            / nullif(abs_instructed + abs_absvd + abs_unexplained_after, 0),
            6
        ) as explained_share_with_absvd,
        round(abs_instructed / nullif(abs_instructed + abs_unexplained_before, 0), 6)
            as q12_instructed_share_same_periods,
        round(abs_unexplained_before - abs_unexplained_after, 1) as fall_mwh,
        round(
            (abs_unexplained_before - abs_unexplained_after)
            / nullif(abs_unexplained_before, 0),
            6
        ) as fall_share
    from figures
),

-- Sections 1 to 4: the registered result, the fall, the months and the
-- placebo.
figure_rows as (
    select
        1 as section,
        1 as position,
        'explained share with ABSVD: the registered result' as item,
        fuel_group,
        settlement_month,
        unit_periods,
        null::bigint as settlement_dates,
        first_date,
        last_date,
        round(abs_instructed, 1) as abs_instructed_deviation_mwh,
        round(abs_absvd, 1) as abs_absvd_mwh,
        round(abs_unexplained_before, 1) as abs_unexplained_before_absvd_mwh,
        round(abs_unexplained_after, 1) as abs_unexplained_after_absvd_mwh,
        explained_share_with_absvd,
        q12_instructed_share_same_periods,
        null::numeric as fall_mwh,
        null::numeric as fall_share,
        null::numeric as value,
        case when explained_share_with_absvd is null then '{{ not_applicable }}' end as detail
    from measured_figures
    where figure_set = 'result'

    union all

    select 1, 2, 'unit-periods with any ABSVD (value: their share)', fuel_group, null,
        unit_periods_with_absvd, null, null, null, null, null, null, null, null, null, null, null,
        round(unit_periods_with_absvd::numeric / nullif(unit_periods, 0), 6), null
    from measured_figures
    where figure_set = 'result'

    union all

    select 2, 1, 'fall in unexplained energy once ABSVD is counted: {{ fall_label }}',
        fuel_group, null, unit_periods, null, null, null, null, null,
        round(abs_unexplained_before, 1), round(abs_unexplained_after, 1), null, null,
        fall_mwh, fall_share, null,
        case when fall_share is null then '{{ not_applicable }}' end
    from measured_figures
    where figure_set = 'result'

    union all

    select 3, 1, 'by calendar month of the settlement date: {{ month_label }}',
        fuel_group, settlement_month, unit_periods, null, first_date, last_date,
        round(abs_instructed, 1), round(abs_absvd, 1), round(abs_unexplained_before, 1),
        round(abs_unexplained_after, 1), explained_share_with_absvd, null, fall_mwh, fall_share,
        null, case when explained_share_with_absvd is null then '{{ not_applicable }}' end
    from measured_figures
    where figure_set = 'by month'

    union all

    select 4, 1, 'placebo: ABSVD of the same unit and settlement period on the previous settlement date',
        fuel_group, null, unit_periods, null, null, null,
        round(abs_instructed, 1), round(abs_absvd, 1), round(abs_unexplained_before, 1),
        round(abs_unexplained_after, 1), explained_share_with_absvd, null, fall_mwh, fall_share,
        null,
        case when explained_share_with_absvd is null
            then '{{ not_applicable }}; {{ placebo_detail }}'
            else '{{ placebo_detail }}'
        end
    from measured_figures
    where figure_set = 'placebo'

    union all

    select 4, 2, 'real figures on the placebo''s unit-periods',
        fuel_group, null, unit_periods, null, null, null,
        round(abs_instructed, 1), round(abs_absvd, 1), round(abs_unexplained_before, 1),
        round(abs_unexplained_after, 1), explained_share_with_absvd, null, fall_mwh, fall_share,
        null, case when explained_share_with_absvd is null then '{{ not_applicable }}' end
    from measured_figures
    where figure_set = 'real on the placebo''s unit-periods'
),

-- The share cannot fall where unexplained is at least instructed. With
-- I = sum|instructed|, U and U′ = sum|unexplained| before and after ABSVD,
-- and A = sum|ABSVD|, Q26 = (I + A) / (I + A + U′) is at least
-- Q12 = I / (I + U) exactly when A·U ≥ I·(U′ − U). Per period the residual
-- after is the residual before plus ABSVD, so U′ ≤ U + A, and the condition
-- holds for any ABSVD whenever U ≥ I. For such a fuel group a share above
-- Q12's shows nothing; the fall is the informative figure.

-- Unit-periods left out, per fuel group (sections 1 and 4) and overall.
left_out_reasons as (
    select
        reasons.section,
        reasons.position,
        reasons.item,
        reasons.reason
    from (
        values
            (1, 3, 'unit-periods left out: settlement date not captured', 'settlement date not captured'),
            (1, 4, 'unit-periods left out: latest capture {{ young_capture }} the settlement date', 'capture too young'),
            (4, 3, 'left out of the placebo: the previous settlement date has no such settlement period', 'no such period'),
            (4, 4, 'left out of the placebo: the previous settlement date was not captured', 'previous date not captured'),
            (4, 5, 'left out of the placebo: the previous settlement date''s latest capture is {{ young_capture }} it', 'previous capture too young')
    ) as reasons (section, position, item, reason)
),

left_out_unit_periods as (
    select fuel_group, status as reason
    from unit_periods

    union all

    select listed.fuel_group, placebo.placebo_status
    from placebo_unit_periods as placebo
    cross join lateral (values (placebo.fuel_group), ('{{ all_fuel_groups }}')) as listed (fuel_group)
),

left_out_rows as (
    select
        reasons.section,
        reasons.position,
        reasons.item,
        listed.fuel_group,
        null::date as settlement_month,
        count(left_out.reason) as unit_periods,
        null::bigint as settlement_dates,
        null::date as first_date,
        null::date as last_date,
        null::numeric as abs_instructed_deviation_mwh,
        null::numeric as abs_absvd_mwh,
        null::numeric as abs_unexplained_before_absvd_mwh,
        null::numeric as abs_unexplained_after_absvd_mwh,
        null::numeric as explained_share_with_absvd,
        null::numeric as q12_instructed_share_same_periods,
        null::numeric as fall_mwh,
        null::numeric as fall_share,
        null::numeric as value,
        null::text as detail
    from left_out_reasons as reasons
    inner join (
        select fuel_group from fuel_groups
        union all
        select '{{ all_fuel_groups }}'
    ) as listed
        on reasons.section = 4 or listed.fuel_group <> '{{ all_fuel_groups }}'
    left join left_out_unit_periods as left_out
        on left_out.fuel_group = listed.fuel_group
        and left_out.reason = reasons.reason
    group by reasons.section, reasons.position, reasons.item, listed.fuel_group
),

-- Section 5: capture age, over all fuel groups.
capture_rows as (
    select
        5 as section,
        1 as position,
        'settlement dates captured' as item,
        null::text as fuel_group,
        null::date as settlement_month,
        null::bigint as unit_periods,
        count(*) as settlement_dates,
        min(settlement_date) as first_date,
        max(settlement_date) as last_date,
        null::numeric as abs_instructed_deviation_mwh,
        null::numeric as abs_absvd_mwh,
        null::numeric as abs_unexplained_before_absvd_mwh,
        null::numeric as abs_unexplained_after_absvd_mwh,
        null::numeric as explained_share_with_absvd,
        null::numeric as q12_instructed_share_same_periods,
        null::numeric as fall_mwh,
        null::numeric as fall_share,
        null::numeric as value,
        null::text as detail
    from dated_captures

    union all

    select 5, 2, 'settlement dates counted: latest capture''s UTC date at least '
        || {{ minimum_capture_age_days }} || ' days after the settlement date',
        null, null, null, count(*), min(settlement_date), max(settlement_date),
        null, null, null, null, null, null, null, null, null,
        'each unit-period''s ABSVD is from the latest capture holding it; not called final'
    from counted_dates

    union all

    select 5, 3, 'settlement dates left out: latest capture {{ young_capture }} the settlement date',
        null, null,
        (select count(*) from unit_periods where status = 'capture too young'),
        count(*), min(settlement_date), max(settlement_date),
        null, null, null, null, null, null, null, null, null, null
    from dated_captures
    where not is_old_enough

    union all

    select 5, 4, 'unit-periods left out: settlement date not captured', null, null,
        count(*), null, null, null, null, null, null, null, null, null, null, null, null, null
    from unit_periods
    where status = 'settlement date not captured'
),

-- Section 6: the active cohort, over all fuel groups. Idle unit-periods are
-- the result's unit-periods with PN 0 and metered 0, both exact.
cohort_rows as (
    select
        6 as section,
        1 as position,
        'cohort units: active in the window (value: units)' as item,
        null::text as fuel_group,
        null::date as settlement_month,
        null::bigint as unit_periods,
        null::bigint as settlement_dates,
        (select min(settlement_date) from counted_dates) as first_date,
        null::date as last_date,
        null::numeric as abs_instructed_deviation_mwh,
        null::numeric as abs_absvd_mwh,
        null::numeric as abs_unexplained_before_absvd_mwh,
        null::numeric as abs_unexplained_after_absvd_mwh,
        null::numeric as explained_share_with_absvd,
        null::numeric as q12_instructed_share_same_periods,
        null::numeric as fall_mwh,
        null::numeric as fall_share,
        count(*) filter (where cohort_status = 'active')::numeric as value,
        null::text as detail
    from cohort_units

    union all

    select 6, 2, 'cohort units: interconnectors, fuel type beginning INT, left out (value: units)',
        null, null, null, null, null, null, null, null, null, null, null, null, null, null,
        count(*) filter (where cohort_status = 'interconnector'), null
    from cohort_units

    union all

    select 6, 3, 'of which INTELE, an interconnector by assumption (value: units)',
        null, null, null, null, null, null, null, null, null, null, null, null, null, null,
        count(*) filter (where cohort_status = 'interconnector' and fuel_type = 'INTELE'), null
    from cohort_units

    union all

    select 6, 4, 'cohort units: inactive in the window, left out (value: units)',
        null, null, null, null, null, null, null, null, null, null, null, null, null, null,
        count(*) filter (where cohort_status = 'inactive'), null
    from cohort_units

    union all

    select 6, 5, 'unit-periods left out: interconnector units', null, null,
        count(*) filter (where status = 'interconnector unit'),
        null, null, null, null, null, null, null, null, null, null, null, null, null
    from unit_periods

    union all

    select 6, 6, 'unit-periods left out: inactive units', null, null,
        count(*) filter (where status = 'inactive unit'),
        null, null, null, null, null, null, null, null, null, null, null, null, null
    from unit_periods

    union all

    select 6, 7, 'idle unit-periods in the result: PN 0 and metered 0 (value: their share)',
        null, null,
        count(*) filter (where pn_mwh = 0 and metered_mwh = 0),
        null, null, null, null, null, null, null, null, null, null, null,
        round(count(*) filter (where pn_mwh = 0 and metered_mwh = 0)::numeric / nullif(count(*), 0), 6),
        null
    from result_unit_periods
)

select
    section,
    position,
    item,
    fuel_group,
    settlement_month,
    unit_periods,
    settlement_dates,
    first_date,
    last_date,
    abs_instructed_deviation_mwh,
    abs_absvd_mwh,
    abs_unexplained_before_absvd_mwh,
    abs_unexplained_after_absvd_mwh,
    explained_share_with_absvd,
    q12_instructed_share_same_periods,
    fall_mwh,
    fall_share,
    value,
    detail
from (
    select * from figure_rows
    union all
    select * from left_out_rows
    union all
    select * from capture_rows
    union all
    select * from cohort_rows
) as output_rows
order by section, fuel_group nulls first, settlement_month nulls first, position
