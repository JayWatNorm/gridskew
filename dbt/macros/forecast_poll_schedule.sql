{#
    The capture schedules of the forecast archive (var forecast_poll_schedules).
    A poll belongs to the last schedule whose from_slot is at or before its
    time: forecast_poll_schedule gives that schedule's label and
    forecast_schedule_full_poll_rows the half-hours a full poll holds on it.
    Each is a case on poll_time, a SQL expression, so the boundary lives in
    the var alone. A null poll time has no schedule.
#}

{% macro forecast_poll_schedules() %}
    {#- The var, checked: the first schedule has no from_slot, and each later
        one starts after the one before, at a UTC time with its offset. -#}
    {%- set schedules = var('forecast_poll_schedules') -%}
    {%- set utc_timestamp = '^\\d{4}-\\d{2}-\\d{2} \\d{2}:\\d{2}:\\d{2}[+]00$' -%}
    {%- for schedule in schedules -%}
        {%- if loop.first -%}
            {%- set is_valid = schedule.from_slot is none -%}
        {%- else -%}
            {%- set previous = schedules[loop.index0 - 1] -%}
            {%- set is_valid = schedule.from_slot is not none
                and modules.re.match(utc_timestamp, schedule.from_slot | string)
                and (previous.from_slot is none or previous.from_slot < schedule.from_slot) -%}
        {%- endif -%}
        {%- if not is_valid -%}
            {{ exceptions.raise_compiler_error(
                "forecast_poll_schedules must start with a schedule without from_slot, "
                ~ "then list later ones in time order, each from_slot a UTC time such as "
                ~ "'2026-09-10 11:00:00+00': " ~ schedule
            ) }}
        {%- endif -%}
    {%- endfor -%}
    {{ return(schedules) }}
{% endmacro %}

{% macro forecast_poll_schedule_case(poll_time, field) %}
    case
    {%- for schedule in forecast_poll_schedules() | reverse %}
        {%- set value = schedule[field] %}
        {%- set sql_value = "'" ~ (value | replace("'", "''")) ~ "'" if value is string else value %}
        {%- if schedule.from_slot is none %}
        when {{ poll_time }} is not null then {{ sql_value }}
        {%- else %}
        when {{ poll_time }} >= timestamptz '{{ schedule.from_slot }}' then {{ sql_value }}
        {%- endif %}
    {%- endfor %}
    end
{%- endmacro %}

{% macro forecast_poll_schedule(poll_time) %}
    {{- forecast_poll_schedule_case(poll_time, 'label') }}
{%- endmacro %}

{% macro forecast_schedule_full_poll_rows(poll_time) %}
    {{- forecast_poll_schedule_case(poll_time, 'full_poll_rows') }}
{%- endmacro %}
