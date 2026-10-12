{% macro changed_cohort_units(relation, cohort, commitments) %}

    {#-
        The cohort units whose fuel type or Elexon ID differs from the ones
        stored in the shortfall table, or that have no stored rows yet (they
        joined the cohort), as a list. A unit with no PN period has nothing
        to recompute and is left out. Read before the model runs, like
        capture_watermark: written inside the model, this branch was planned
        as billions of rows every night, though it found none.
    -#}
    {%- if not execute -%}
        {{ return([]) }}
    {%- endif -%}

    {%- set query -%}
        select distinct cohort.national_grid_bm_unit
        from {{ cohort }} as cohort
        left join (
            select distinct national_grid_bm_unit, elexon_bm_unit, fuel_type
            from {{ relation }}
        ) as stored
            on cohort.national_grid_bm_unit = stored.national_grid_bm_unit
        where (
            stored.national_grid_bm_unit is null
            or stored.fuel_type is distinct from cohort.fuel_type
            or stored.elexon_bm_unit is distinct from cohort.elexon_bm_unit
        )
            and exists (
                select 1
                from {{ commitments }} as commitments
                where commitments.national_grid_bm_unit = cohort.national_grid_bm_unit
            )
        order by cohort.national_grid_bm_unit
    {%- endset -%}

    {{ return(run_query(query).columns[0].values() | list) }}

{% endmacro %}
