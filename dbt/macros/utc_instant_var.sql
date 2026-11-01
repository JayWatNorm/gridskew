{% macro utc_instant_var(var_name) %}
    {#- A var that holds an instant: a date and time with an explicit UTC
        offset, such as '2026-11-30 00:00:00+00', or null. Without the offset
        PostgreSQL would read it in the session's time zone, so anything else
        stops the compile and names the var. -#}
    {%- set value = var(var_name) -%}
    {%- set utc_timestamp = '^\\d{4}-\\d{2}-\\d{2}[ T]\\d{2}:\\d{2}(:\\d{2}(\\.\\d+)?)?(Z|[+-]00(:?00)?)$' -%}
    {%- if value is not none and not modules.re.match(utc_timestamp, (value | string) | trim) -%}
        {{ exceptions.raise_compiler_error(
            var_name ~ " must be a UTC date and time with its offset, "
            ~ "such as '2026-11-30 00:00:00+00': " ~ value
        ) }}
    {%- endif -%}
    {{ return(value) }}
{% endmacro %}
