{#
    Energy under a straight MW line, in MWh.

    A ramp runs from level_from at time_from to level_to at time_to.
    ramp_mwh returns the energy of the part of that ramp inside
    [window_start, window_end): the mean of the MW at the two clipped ends,
    times the clipped hours.

    The caller passes only ramps that last longer than zero and overlap the
    window.

    Every argument is a SQL expression passed as text, for example
    ramp_mwh('level_from', 'level_to', 'time_from', 'time_to',
             'period_start_utc', "period_start_utc + interval '30 minutes'").
#}

{% macro ramp_level_at(level_from, level_to, time_from, time_to, at) %}
    (
        ({{ level_from }})::numeric
        + (({{ level_to }}) - ({{ level_from }}))::numeric
            * extract(epoch from (({{ at }}) - ({{ time_from }})))
            / extract(epoch from (({{ time_to }}) - ({{ time_from }})))
    )
{% endmacro %}

{% macro ramp_mwh(level_from, level_to, time_from, time_to, window_start, window_end) %}
    {% set clipped_from = 'greatest(' ~ time_from ~ ', ' ~ window_start ~ ')' %}
    {% set clipped_to = 'least(' ~ time_to ~ ', ' ~ window_end ~ ')' %}
    (
        (
            {{ ramp_level_at(level_from, level_to, time_from, time_to, clipped_from) }}
            + {{ ramp_level_at(level_from, level_to, time_from, time_to, clipped_to) }}
        ) / 2
        * extract(epoch from ({{ clipped_to }} - {{ clipped_from }})) / 3600
    )
{% endmacro %}
