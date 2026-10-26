{#
    Orders half-hours that share a value before ntile() splits them into
    quintiles (Q4 and Q28, amended 26 October 2026 before either was read).
    A tie says nothing about size, so the order inside one must not follow
    the calendar: it is a fixed pseudo-random number from md5 of the
    analysis's seed, the bootstrap draw and pick (0 and 0 for the observed
    sample) and the half-hour's start in epoch seconds, which is the same in
    every session time zone. Callers add period start and pick after it, so
    the order is total.
#}
{% macro quintile_tie_order(seed, draw, pick, period_start) %}
    ('x' || substr(md5('{{ seed }}:' || {{ draw }} || ':' || {{ pick }} || ':'
        || extract(epoch from {{ period_start }})::bigint), 1, 8))::bit(32)::bigint
{%- endmacro %}
