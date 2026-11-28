{{ config(group='analysis') }}

-- Q38: what share of registered nuclear capacity did metered output reach
-- each month, and how much of the gap below it coincided with a published
-- outage notice? Reading rule: research questions, Q38.
--
-- Units: the ten generating units listed below, at the five stations that
-- generated in the months read. A listed unit is measured only when the
-- latest registry holds exactly one NUCLEAR row for it, with an Elexon ID no
-- other registry row shares; the others are counted in section 6. Any other
-- registry unit with fuel type NUCLEAR is reported in section 6 and is in no
-- figure.
--
-- Per unit and half-hour, from the latest settlement run's metered value:
-- output is the metered energy, zero where it is negative (the negative part
-- is drawn energy, reported beside); capacity is the latest registry's
-- generation capacity over the half-hour; the gap is capacity minus output,
-- zero where output is above capacity (that part is reported as above
-- capacity). So capacity = output + gap - above capacity. A second gap is
-- measured below the unit's highest metered half-hour in the months read,
-- or below capacity where that is lower. A unit is generating in a
-- half-hour when its metered value is above zero.
--
-- A half-hour has a notice when int_remit__known_outage_periods lists its
-- unit, Q5's rule: an Active production unavailability was the notice's
-- publication in force at the half-hour's start and its event overlaps the
-- half-hour. Any outage size counts; the gap in half-hours whose notices
-- state no unavailable capacity above zero is reported beside.
--
-- Half-hours: settlement dates from 1 September 2025, the first complete
-- month of metered values held, to the cutoff.
--
-- Section 1 is output against capacity per calendar month of settlement
-- date, section 2 all months together. Sections 3 (per month) and 4 (all
-- months) split both gaps by notice class and by whether the unit was
-- generating. Section 5 is per unit, with its highest metered half-hour and
-- the range of normal capacity its notices' publications state beside the
-- registry capacity. Section 6 is coverage. Section 7 is per unit and
-- month, section 8 the gaps per unit by notice class. Run once, by hand,
-- after the rule is recorded.

{% set read_until = analysis_read_until() %}
{% set first_settlement_date = '2025-09-01' %}
{% set generating_units = [
    ('HEYM11', 'Heysham 1'),
    ('HEYM12', 'Heysham 1'),
    ('HEYM27', 'Heysham 2'),
    ('HEYM28', 'Heysham 2'),
    ('HRTL-1', 'Hartlepool'),
    ('HRTL-2', 'Hartlepool'),
    ('SIZB-1', 'Sizewell B'),
    ('SIZB-2', 'Sizewell B'),
    ('TORN-1', 'Torness'),
    ('TORN-2', 'Torness'),
] %}
{% set unit_count = generating_units | length %}
{% set unit_list -%}
    {%- for unit, station in generating_units -%}
        '{{ unit }}'{{ ", " if not loop.last }}
    {%- endfor -%}
{%- endset %}
{% set output_columns = [
    'section',
    'settlement_month',
    'station',
    'national_grid_bm_unit',
    'notice_class',
    'output_state',
    'item',
    'units',
    'unit_half_hours',
    'capacity_mwh',
    'output_mwh',
    'output_pct_of_capacity',
    'gap_mwh',
    'share_of_gap_pct',
    'gap_below_highest_mwh',
    'share_of_gap_below_highest_pct',
    'above_capacity_mwh',
    'drawn_mwh',
    'registry_capacity_mw',
    'highest_half_hour_mw',
    'notice_normal_capacity_low_mw',
    'notice_normal_capacity_high_mw',
    'notice_publications',
    'first_half_hour',
    'last_half_hour',
] %}

with listed as (
    select
        national_grid_bm_unit,
        station
    from (
        values
        {% for unit, station in generating_units -%}
            ('{{ unit }}', '{{ station }}'){{ "," if not loop.last }}
        {% endfor -%}
    ) as generating_units (national_grid_bm_unit, station)
),

registry as (
    select
        national_grid_bm_unit,
        elexon_bm_unit,
        fuel_type,
        generation_capacity_mw,
        count(*) over (partition by elexon_bm_unit) as rows_with_this_elexon_id
    from {{ ref('int_elexon__bm_units_current') }}
),

nuclear_registry as (
    select
        national_grid_bm_unit,
        elexon_bm_unit,
        generation_capacity_mw,
        rows_with_this_elexon_id,
        count(*) over (partition by national_grid_bm_unit) as nuclear_rows_for_this_unit
    from registry
    where fuel_type = 'NUCLEAR'
),

-- A listed unit with one NUCLEAR registry row and an Elexon ID of its own,
-- so no metered row can be counted twice or for another unit.
measurable_units as (
    select
        listed.national_grid_bm_unit,
        listed.station,
        nuclear_registry.elexon_bm_unit,
        nuclear_registry.generation_capacity_mw
    from listed
    inner join nuclear_registry
        on listed.national_grid_bm_unit = nuclear_registry.national_grid_bm_unit
    where nuclear_registry.nuclear_rows_for_this_unit = 1
        and nuclear_registry.elexon_bm_unit is not null
        and nuclear_registry.rows_with_this_elexon_id = 1
),

listed_metered as (
    select
        measurable_units.station,
        measurable_units.national_grid_bm_unit,
        measurable_units.generation_capacity_mw,
        generation.settlement_date,
        generation.period_start_utc,
        generation.latest_quantity_mwh,
        generation.latest_run_code,
        generation.mapping_status
    from {{ ref('fct_generation') }} as generation
    inner join measurable_units
        on generation.bm_unit = measurable_units.elexon_bm_unit
    where generation.settlement_date >= date '{{ first_settlement_date }}'
{% if read_until is not none %}
        and coalesce(generation.period_start_utc, generation.settlement_date::timestamp at time zone 'Europe/London')
            <= timestamptz '{{ read_until }}'
{% endif %}
),

-- The unit list is written out so the filter reaches the view's own rows.
notices as (
    select
        national_grid_bm_unit,
        period_start_utc,
        known_class
    from {{ ref('int_remit__known_outage_periods') }}
    where national_grid_bm_unit in ({{ unit_list }})
),

-- Active production unavailability publications that name a listed unit and
-- whose event overlaps the months read.
unit_publications as materialized (
    select
        national_grid_bm_unit,
        valid_from,
        valid_to,
        event_start_time,
        event_end_time,
        normal_capacity,
        unavailable_capacity
    from {{ ref('int_remit__revisions') }}
    where mapping_rule in ('asset_id', 'affected_unit')
        and message_type = 'UnavailabilitiesOfElectricityFacilities'
        and event_type = 'Production unavailability'
        and event_status = 'Active'
        and national_grid_bm_unit in ({{ unit_list }})
        and (
            event_end_time is null
            or event_end_time
            > date '{{ first_settlement_date }}'::timestamp at time zone 'Europe/London'
        )
{% if read_until is not none %}
        and event_start_time < timestamptz '{{ read_until }}' + interval '30 minutes'
{% endif %}
),

unit_half_hours as (
    select
        listed_metered.station,
        listed_metered.national_grid_bm_unit,
        date_trunc('month', listed_metered.settlement_date)::date as settlement_month,
        listed_metered.period_start_utc,
        listed_metered.latest_run_code,
        listed_metered.generation_capacity_mw,
        listed_metered.latest_quantity_mwh,
        listed_metered.generation_capacity_mw * 0.5 as capacity_mwh,
        greatest(listed_metered.latest_quantity_mwh, 0) as output_mwh,
        greatest(-listed_metered.latest_quantity_mwh, 0) as drawn_mwh,
        case
            when listed_metered.latest_quantity_mwh > 0 then 'generating'
            else 'not generating'
        end as output_state,
        coalesce(notices.known_class, 'none') as notice_class,
        -- A publication in force at the half-hour's start, its event
        -- overlapping the half-hour, that states unavailable capacity.
        exists (
            select 1
            from unit_publications as stated
            where stated.national_grid_bm_unit = listed_metered.national_grid_bm_unit
                and stated.unavailable_capacity > 0
                and stated.valid_from <= listed_metered.period_start_utc
                and (
                    stated.valid_to is null
                    or listed_metered.period_start_utc < stated.valid_to
                )
                and stated.event_start_time
                < listed_metered.period_start_utc + interval '30 minutes'
                and (
                    stated.event_end_time is null
                    or stated.event_end_time > listed_metered.period_start_utc
                )
        ) as has_notice_stating_unavailable_capacity
    from listed_metered
    left join notices
        on listed_metered.national_grid_bm_unit = notices.national_grid_bm_unit
        and listed_metered.period_start_utc = notices.period_start_utc
    where listed_metered.period_start_utc is not null
        and listed_metered.latest_quantity_mwh is not null
        and listed_metered.mapping_status = 'mapped'
        and listed_metered.generation_capacity_mw > 0
),

measured as (
    select
        station,
        national_grid_bm_unit,
        settlement_month,
        period_start_utc,
        latest_run_code,
        generation_capacity_mw,
        latest_quantity_mwh,
        capacity_mwh,
        output_mwh,
        drawn_mwh,
        output_state,
        notice_class,
        has_notice_stating_unavailable_capacity,
        greatest(capacity_mwh - output_mwh, 0) as gap_mwh,
        greatest(output_mwh - capacity_mwh, 0) as above_capacity_mwh,
        greatest(
            least(
                capacity_mwh,
                max(output_mwh) over (partition by national_grid_bm_unit)
            ) - output_mwh,
            0
        ) as gap_below_highest_mwh
    from unit_half_hours
),

output_by_month as (
    select
        1 as section,
        settlement_month,
        null::text as station,
        null::text as national_grid_bm_unit,
        null::text as notice_class,
        null::text as output_state,
        'output against registered capacity' as item,
        count(distinct national_grid_bm_unit) as units,
        count(*) as unit_half_hours,
        round(sum(capacity_mwh), 1) as capacity_mwh,
        round(sum(output_mwh), 1) as output_mwh,
        round(100 * sum(output_mwh) / sum(capacity_mwh), 2) as output_pct_of_capacity,
        round(sum(gap_mwh), 1) as gap_mwh,
        null::numeric as share_of_gap_pct,
        round(sum(gap_below_highest_mwh), 1) as gap_below_highest_mwh,
        null::numeric as share_of_gap_below_highest_pct,
        round(sum(above_capacity_mwh), 1) as above_capacity_mwh,
        round(sum(drawn_mwh), 1) as drawn_mwh,
        null::numeric as registry_capacity_mw,
        null::numeric as highest_half_hour_mw,
        null::numeric as notice_normal_capacity_low_mw,
        null::numeric as notice_normal_capacity_high_mw,
        null::bigint as notice_publications,
        min(period_start_utc) as first_half_hour,
        max(period_start_utc) as last_half_hour
    from measured
    group by settlement_month
),

output_all_months as (
    select
        2 as section,
        null::date as settlement_month,
        null::text as station,
        null::text as national_grid_bm_unit,
        null::text as notice_class,
        null::text as output_state,
        'output against registered capacity' as item,
        count(distinct national_grid_bm_unit) as units,
        count(*) as unit_half_hours,
        round(sum(capacity_mwh), 1) as capacity_mwh,
        round(sum(output_mwh), 1) as output_mwh,
        round(100 * sum(output_mwh) / sum(capacity_mwh), 2) as output_pct_of_capacity,
        round(sum(gap_mwh), 1) as gap_mwh,
        null::numeric as share_of_gap_pct,
        round(sum(gap_below_highest_mwh), 1) as gap_below_highest_mwh,
        null::numeric as share_of_gap_below_highest_pct,
        round(sum(above_capacity_mwh), 1) as above_capacity_mwh,
        round(sum(drawn_mwh), 1) as drawn_mwh,
        null::numeric as registry_capacity_mw,
        null::numeric as highest_half_hour_mw,
        null::numeric as notice_normal_capacity_low_mw,
        null::numeric as notice_normal_capacity_high_mw,
        null::bigint as notice_publications,
        min(period_start_utc) as first_half_hour,
        max(period_start_utc) as last_half_hour
    from measured
    having count(*) > 0
),

gap_by_month as (
    select
        3 as section,
        settlement_month,
        null::text as station,
        null::text as national_grid_bm_unit,
        notice_class,
        output_state,
        'gap below registered capacity' as item,
        count(distinct national_grid_bm_unit) as units,
        count(*) as unit_half_hours,
        null::numeric as capacity_mwh,
        null::numeric as output_mwh,
        null::numeric as output_pct_of_capacity,
        round(sum(gap_mwh), 1) as gap_mwh,
        round(
            100 * sum(gap_mwh)
            / nullif(sum(sum(gap_mwh)) over (partition by settlement_month), 0),
            2
        ) as share_of_gap_pct,
        round(sum(gap_below_highest_mwh), 1) as gap_below_highest_mwh,
        round(
            100 * sum(gap_below_highest_mwh)
            / nullif(
                sum(sum(gap_below_highest_mwh)) over (partition by settlement_month),
                0
            ),
            2
        ) as share_of_gap_below_highest_pct,
        null::numeric as above_capacity_mwh,
        null::numeric as drawn_mwh,
        null::numeric as registry_capacity_mw,
        null::numeric as highest_half_hour_mw,
        null::numeric as notice_normal_capacity_low_mw,
        null::numeric as notice_normal_capacity_high_mw,
        null::bigint as notice_publications,
        null::timestamptz as first_half_hour,
        null::timestamptz as last_half_hour
    from measured
    group by settlement_month, notice_class, output_state
),

gap_all_months as (
    select
        4 as section,
        null::date as settlement_month,
        null::text as station,
        null::text as national_grid_bm_unit,
        notice_class,
        output_state,
        'gap below registered capacity' as item,
        count(distinct national_grid_bm_unit) as units,
        count(*) as unit_half_hours,
        null::numeric as capacity_mwh,
        null::numeric as output_mwh,
        null::numeric as output_pct_of_capacity,
        round(sum(gap_mwh), 1) as gap_mwh,
        round(100 * sum(gap_mwh) / nullif(sum(sum(gap_mwh)) over (), 0), 2)
            as share_of_gap_pct,
        round(sum(gap_below_highest_mwh), 1) as gap_below_highest_mwh,
        round(
            100 * sum(gap_below_highest_mwh)
            / nullif(sum(sum(gap_below_highest_mwh)) over (), 0),
            2
        ) as share_of_gap_below_highest_pct,
        null::numeric as above_capacity_mwh,
        null::numeric as drawn_mwh,
        null::numeric as registry_capacity_mw,
        null::numeric as highest_half_hour_mw,
        null::numeric as notice_normal_capacity_low_mw,
        null::numeric as notice_normal_capacity_high_mw,
        null::bigint as notice_publications,
        null::timestamptz as first_half_hour,
        null::timestamptz as last_half_hour
    from measured
    group by notice_class, output_state
),

gap_with_no_stated_unavailable_capacity as (
    select
        4 as section,
        null::date as settlement_month,
        null::text as station,
        null::text as national_grid_bm_unit,
        notice_class,
        output_state,
        'gap with notices that state no unavailable capacity above zero' as item,
        count(distinct national_grid_bm_unit) as units,
        count(*) as unit_half_hours,
        null::numeric as capacity_mwh,
        null::numeric as output_mwh,
        null::numeric as output_pct_of_capacity,
        round(sum(gap_mwh), 1) as gap_mwh,
        null::numeric as share_of_gap_pct,
        round(sum(gap_below_highest_mwh), 1) as gap_below_highest_mwh,
        null::numeric as share_of_gap_below_highest_pct,
        null::numeric as above_capacity_mwh,
        null::numeric as drawn_mwh,
        null::numeric as registry_capacity_mw,
        null::numeric as highest_half_hour_mw,
        null::numeric as notice_normal_capacity_low_mw,
        null::numeric as notice_normal_capacity_high_mw,
        null::bigint as notice_publications,
        null::timestamptz as first_half_hour,
        null::timestamptz as last_half_hour
    from measured
    where notice_class <> 'none'
        and not has_notice_stating_unavailable_capacity
    group by notice_class, output_state
),

notice_capacities as (
    select
        national_grid_bm_unit,
        min(normal_capacity) as normal_capacity_low_mw,
        max(normal_capacity) as normal_capacity_high_mw,
        count(*) as publications
    from unit_publications
    group by national_grid_bm_unit
),

by_unit as (
    select
        5 as section,
        null::date as settlement_month,
        measured.station,
        measured.national_grid_bm_unit,
        null::text as notice_class,
        null::text as output_state,
        'unit, all months' as item,
        1::bigint as units,
        count(*) as unit_half_hours,
        round(sum(measured.capacity_mwh), 1) as capacity_mwh,
        round(sum(measured.output_mwh), 1) as output_mwh,
        round(100 * sum(measured.output_mwh) / sum(measured.capacity_mwh), 2)
            as output_pct_of_capacity,
        round(sum(measured.gap_mwh), 1) as gap_mwh,
        null::numeric as share_of_gap_pct,
        round(sum(measured.gap_below_highest_mwh), 1) as gap_below_highest_mwh,
        null::numeric as share_of_gap_below_highest_pct,
        round(sum(measured.above_capacity_mwh), 1) as above_capacity_mwh,
        round(sum(measured.drawn_mwh), 1) as drawn_mwh,
        max(measured.generation_capacity_mw) as registry_capacity_mw,
        max(measured.latest_quantity_mwh) * 2 as highest_half_hour_mw,
        max(notice_capacities.normal_capacity_low_mw) as notice_normal_capacity_low_mw,
        max(notice_capacities.normal_capacity_high_mw) as notice_normal_capacity_high_mw,
        max(notice_capacities.publications) as notice_publications,
        min(measured.period_start_utc) as first_half_hour,
        max(measured.period_start_utc) as last_half_hour
    from measured
    left join notice_capacities
        on measured.national_grid_bm_unit = notice_capacities.national_grid_bm_unit
    group by measured.station, measured.national_grid_bm_unit
),

by_unit_and_month as (
    select
        7 as section,
        settlement_month,
        station,
        national_grid_bm_unit,
        null::text as notice_class,
        null::text as output_state,
        'unit, by month' as item,
        1::bigint as units,
        count(*) as unit_half_hours,
        round(sum(capacity_mwh), 1) as capacity_mwh,
        round(sum(output_mwh), 1) as output_mwh,
        round(100 * sum(output_mwh) / sum(capacity_mwh), 2) as output_pct_of_capacity,
        round(sum(gap_mwh), 1) as gap_mwh,
        null::numeric as share_of_gap_pct,
        round(sum(gap_below_highest_mwh), 1) as gap_below_highest_mwh,
        null::numeric as share_of_gap_below_highest_pct,
        round(sum(above_capacity_mwh), 1) as above_capacity_mwh,
        round(sum(drawn_mwh), 1) as drawn_mwh,
        null::numeric as registry_capacity_mw,
        null::numeric as highest_half_hour_mw,
        null::numeric as notice_normal_capacity_low_mw,
        null::numeric as notice_normal_capacity_high_mw,
        null::bigint as notice_publications,
        min(period_start_utc) as first_half_hour,
        max(period_start_utc) as last_half_hour
    from measured
    group by settlement_month, station, national_grid_bm_unit
),

gap_by_unit as (
    select
        8 as section,
        null::date as settlement_month,
        station,
        national_grid_bm_unit,
        notice_class,
        output_state,
        'gap by unit' as item,
        1::bigint as units,
        count(*) as unit_half_hours,
        null::numeric as capacity_mwh,
        null::numeric as output_mwh,
        null::numeric as output_pct_of_capacity,
        round(sum(gap_mwh), 1) as gap_mwh,
        round(
            100 * sum(gap_mwh)
            / nullif(sum(sum(gap_mwh)) over (partition by national_grid_bm_unit), 0),
            2
        ) as share_of_gap_pct,
        round(sum(gap_below_highest_mwh), 1) as gap_below_highest_mwh,
        round(
            100 * sum(gap_below_highest_mwh)
            / nullif(
                sum(sum(gap_below_highest_mwh))
                    over (partition by national_grid_bm_unit),
                0
            ),
            2
        ) as share_of_gap_below_highest_pct,
        null::numeric as above_capacity_mwh,
        null::numeric as drawn_mwh,
        null::numeric as registry_capacity_mw,
        null::numeric as highest_half_hour_mw,
        null::numeric as notice_normal_capacity_low_mw,
        null::numeric as notice_normal_capacity_high_mw,
        null::bigint as notice_publications,
        null::timestamptz as first_half_hour,
        null::timestamptz as last_half_hour
    from measured
    group by station, national_grid_bm_unit, notice_class, output_state
),

-- Every half-hour from the first settlement date's start to the cutoff,
-- whether or not any unit has a metered value in it.
half_hours_to_read as (
    select half_hours.period_start_utc
    from generate_series(
        date '{{ first_settlement_date }}'::timestamp at time zone 'Europe/London',
{% if read_until is not none %}
        timestamptz '{{ read_until }}',
{% else %}
        (select max(period_start_utc) from measured),
{% endif %}
        interval '30 minutes'
    ) as half_hours (period_start_utc)
),

half_hour_units as (
    select
        period_start_utc,
        count(distinct national_grid_bm_unit) as units_measured
    from measured
    group by period_start_utc
),

coverage as (
    select
        6 as section,
        null::text as national_grid_bm_unit,
        'unit half-hours by metered run: ' || latest_run_code as item,
        count(distinct national_grid_bm_unit) as units,
        count(*) as unit_half_hours,
        round(sum(output_mwh), 1) as output_mwh,
        round(sum(drawn_mwh), 1) as drawn_mwh,
        null::numeric as registry_capacity_mw
    from measured
    group by latest_run_code

    union all

    select
        6,
        null,
        'left out: no period start',
        count(distinct national_grid_bm_unit),
        count(*),
        null,
        null,
        null
    from listed_metered
    where period_start_utc is null

    union all

    select
        6,
        null,
        'left out: no metered value',
        count(distinct national_grid_bm_unit),
        count(*),
        null,
        null,
        null
    from listed_metered
    where period_start_utc is not null
        and latest_quantity_mwh is null

    union all

    select
        6,
        null,
        'left out: mapping status ' || mapping_status,
        count(distinct national_grid_bm_unit),
        count(*),
        null,
        null,
        null
    from listed_metered
    where period_start_utc is not null
        and latest_quantity_mwh is not null
        and mapping_status <> 'mapped'
    group by mapping_status

    union all

    select
        6,
        null,
        'left out: no positive registry capacity',
        count(distinct national_grid_bm_unit),
        count(*),
        null,
        null,
        null
    from listed_metered
    where period_start_utc is not null
        and latest_quantity_mwh is not null
        and mapping_status = 'mapped'
        and (generation_capacity_mw is null or generation_capacity_mw <= 0)

    union all

    select
        6,
        null,
        'half-hours from the first settlement date to the cutoff',
        null,
        count(*),
        null,
        null,
        null
    from half_hours_to_read

    union all

    select
        6,
        null,
        'half-hours without all {{ unit_count }} listed units measured',
        null,
        count(*),
        null,
        null,
        null
    from half_hours_to_read
    left join half_hour_units
        on half_hours_to_read.period_start_utc = half_hour_units.period_start_utc
    where coalesce(half_hour_units.units_measured, 0) < {{ unit_count }}

    union all

    select
        6,
        null,
        'listed units left out: no single NUCLEAR registry row with an Elexon ID of its own',
        count(*),
        null,
        null,
        null,
        null
    from listed
    where national_grid_bm_unit not in (
        select national_grid_bm_unit from measurable_units
    )

    union all

    select
        6,
        nuclear_registry.national_grid_bm_unit,
        'registry NUCLEAR unit outside the listed units',
        1,
        count(generation.bm_unit),
        round(sum(greatest(generation.latest_quantity_mwh, 0)), 1),
        round(sum(greatest(-generation.latest_quantity_mwh, 0)), 1),
        max(nuclear_registry.generation_capacity_mw)
    from nuclear_registry
    left join {{ ref('fct_generation') }} as generation
        on nuclear_registry.elexon_bm_unit = generation.bm_unit
        and generation.settlement_date >= date '{{ first_settlement_date }}'
{% if read_until is not none %}
        and coalesce(generation.period_start_utc, generation.settlement_date::timestamp at time zone 'Europe/London')
            <= timestamptz '{{ read_until }}'
{% endif %}
    where nuclear_registry.national_grid_bm_unit not in (
        select national_grid_bm_unit from listed
    )
    group by nuclear_registry.national_grid_bm_unit
),

coverage_rows as (
    select
        section,
        null::date as settlement_month,
        null::text as station,
        national_grid_bm_unit,
        null::text as notice_class,
        null::text as output_state,
        item,
        units,
        unit_half_hours,
        null::numeric as capacity_mwh,
        output_mwh,
        null::numeric as output_pct_of_capacity,
        null::numeric as gap_mwh,
        null::numeric as share_of_gap_pct,
        null::numeric as gap_below_highest_mwh,
        null::numeric as share_of_gap_below_highest_pct,
        null::numeric as above_capacity_mwh,
        drawn_mwh,
        registry_capacity_mw,
        null::numeric as highest_half_hour_mw,
        null::numeric as notice_normal_capacity_low_mw,
        null::numeric as notice_normal_capacity_high_mw,
        null::bigint as notice_publications,
        null::timestamptz as first_half_hour,
        null::timestamptz as last_half_hour
    from coverage
)

{% for section_rows in [
    'output_by_month',
    'output_all_months',
    'gap_by_month',
    'gap_all_months',
    'gap_with_no_stated_unavailable_capacity',
    'by_unit',
    'coverage_rows',
    'by_unit_and_month',
    'gap_by_unit',
] -%}
select
    {{ output_columns | join(',\n    ') }}
from {{ section_rows }}
{{ "union all" if not loop.last }}
{% endfor -%}
order by 1, 2, 3, 4, 5, 6, 7
