{% macro analysis_read_until() %}
    {#- The fixed cutoff of a one-look analysis: a date and time with an
        explicit UTC offset, such as '2026-09-30 22:30:00+00'. Without the
        offset PostgreSQL would read it in the session's time zone and move
        every population's edge by an hour in summer, so anything else stops
        the compile. -#}
    {%- set value = var('analysis_read_until') -%}
    {%- set utc_timestamp = '^\\d{4}-\\d{2}-\\d{2}[ T]\\d{2}:\\d{2}(:\\d{2}(\\.\\d+)?)?(Z|[+-]00(:?00)?)$' -%}
    {%- if value is not none and not modules.re.match(utc_timestamp, (value | string) | trim) -%}
        {{ exceptions.raise_compiler_error(
            "analysis_read_until must be a UTC date and time with its offset, "
            ~ "such as '2026-09-30 22:30:00+00': " ~ value
        ) }}
    {%- endif -%}
    {{ return(value) }}
{% endmacro %}
