{#
    Every unit of the analysis cohort (int_elexon__bm_unit_cohort) with its
    status in a question's own window: the active cohort, for unit-delivery
    questions read from 31 October 2026. It renders a subquery that runs
    inside the calling analysis, one row per cohort unit:
    national_grid_bm_unit, fuel_type, fuel_group, cohort_status and
    has_row_in_window. A unit with no row in the window is still returned.

    - interconnector: a fuel type beginning INT, decided first, whatever the
      unit did (the fuel-code seed maps INTELE to unclassified, so this
      treats INTELE as an interconnector by assumption);
    - active: otherwise, at least one half-hour in the window with a PN above
      0, or a metered value above 0 in the first or the latest settlement run
      held. Any half-hour counts, determinable or not;
    - inactive: the rest (no row in the window, active only outside it, or
      only importing).

    Activity is read from the two protected period views, mapped as the
    shortfall table maps them, not from the shortfall table, which holds
    only unit-periods with a PN. window_start and window_end are SQL
    expressions for inclusive timestamptz bounds on period_start_utc;
    window_end none means no upper bound. metered_periods, if given, names a
    relation of the calling query with int_elexon__b1610_period_mapped's
    columns (bm_unit, settlement_date, period_start_utc, first_quantity_mwh,
    latest_quantity_mwh, mapping_status, mapped_national_grid_bm_unit) read
    in its place, such as one built from the rows held at a capture cutoff.
    A period with no period_start_utc
    falls in no window. A settlement-date range one day wider than the
    window on each side lets the period tables' indexes on unit and date
    serve each test; the period_start_utc test decides.
#}

{% macro active_cohort_window_test(alias, window_start, window_end) %}
    {{ alias }}.settlement_date >= (({{ window_start }}) at time zone 'Europe/London')::date - 1
    and {{ alias }}.period_start_utc >= {{ window_start }}
    {%- if window_end is not none %}
    and {{ alias }}.settlement_date <= (({{ window_end }}) at time zone 'Europe/London')::date + 1
    and {{ alias }}.period_start_utc <= {{ window_end }}
    {%- endif %}
{% endmacro %}

{% macro active_cohort_units(window_start, window_end, metered_periods=none) %}
    {%- if metered_periods is none -%}
        {%- set metered_relation = ref('int_elexon__b1610_period_mapped') -%}
    {%- else -%}
        {%- set metered_relation = metered_periods -%}
    {%- endif %}
    (
        select
            cohort.national_grid_bm_unit,
            cohort.fuel_type,
            cohort.fuel_group,
            case
                when cohort.fuel_type like 'INT%' then 'interconnector'
                when exists (
                    select 1
                    from {{ ref('int_elexon__pn_period_mapped') }} as notified
                    where notified.national_grid_bm_unit = cohort.national_grid_bm_unit
                        and notified.mapping_status = 'mapped'
                        and notified.pn_mwh > 0
                        and {{ active_cohort_window_test('notified', window_start, window_end) }}
                )
                    or exists (
                        select 1
                        from {{ metered_relation }} as metered
                        where metered.bm_unit = cohort.elexon_bm_unit
                            and metered.mapping_status = 'mapped'
                            and metered.mapped_national_grid_bm_unit = cohort.national_grid_bm_unit
                            and (metered.first_quantity_mwh > 0 or metered.latest_quantity_mwh > 0)
                            and {{ active_cohort_window_test('metered', window_start, window_end) }}
                    )
                    then 'active'
                else 'inactive'
            end as cohort_status,
            exists (
                select 1
                from {{ ref('int_elexon__pn_period_mapped') }} as notified
                where notified.national_grid_bm_unit = cohort.national_grid_bm_unit
                    and notified.mapping_status = 'mapped'
                    and {{ active_cohort_window_test('notified', window_start, window_end) }}
            )
                or exists (
                    select 1
                    from {{ metered_relation }} as metered
                    where metered.bm_unit = cohort.elexon_bm_unit
                        and metered.mapping_status = 'mapped'
                        and metered.mapped_national_grid_bm_unit = cohort.national_grid_bm_unit
                        and {{ active_cohort_window_test('metered', window_start, window_end) }}
                ) as has_row_in_window
        from {{ ref('int_elexon__bm_unit_cohort') }} as cohort
    )
{% endmacro %}
