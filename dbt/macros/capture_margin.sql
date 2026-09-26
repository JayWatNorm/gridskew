{% macro capture_margin() %}

    {#-
        How far behind the stored watermark an incremental period model looks
        for new source captures. It must exceed the longest gap between a
        poller stamping retrieved_at and committing (the longest Airflow task
        timeout, 45 minutes today). Default: 3 days.
    -#}
    {%- set margin = var('capture_margin', '3 days') | string | trim -%}
    {%- if not modules.re.fullmatch('[1-9][0-9]* (minute|minutes|hour|hours|day|days)', margin) -%}
        {{ exceptions.raise_compiler_error(
            "capture_margin must be a positive whole number of minutes, hours "
            ~ "or days, for example '3 days'; got '" ~ margin ~ "'"
        ) }}
    {%- endif -%}
    interval '{{ margin }}'

{% endmacro %}
