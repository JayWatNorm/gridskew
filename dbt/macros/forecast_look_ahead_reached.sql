{% macro forecast_look_ahead_reached(read_until) %}
    {#- True once enough slot polls are stored after the cutoff to settle a
        forecast stall that runs past it: one fewer than the run of unchanged
        polls the stall rule needs, wherever slots are missing. False when
        there is no cutoff. -#}
    {%- if read_until is none -%}
        false
    {%- else -%}
        (
            select count(distinct poll_slot)
            from {{ ref('int_carbon_forecast__revisions') }}
            where is_first_poll_in_slot
                and poll_slot > timestamptz '{{ read_until }}'
        ) >= {{ var('forecast_no_change_run_polls') }} - 1
    {%- endif -%}
{% endmacro %}
