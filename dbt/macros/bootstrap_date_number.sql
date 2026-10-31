{#
    The date a day-block bootstrap pick takes, for the one-look analyses read
    from 31 October 2026 (Q1's beside analysis, Q10 and Q4). Dates are
    numbered 1 to n in ascending order, and draws and picks are counted from
    1. The pick is the first 8 hex digits of md5('<seed>:<draw>:<pick>') as an
    integer, modulo the number of dates, plus 1, so every run and every
    PostgreSQL gives the same draws. seed is written into the SQL as text;
    draw, pick and dates are SQL expressions.
#}
{% macro bootstrap_date_number(seed, draw, pick, dates) %}
    1 + mod(
        ('x' || substr(md5('{{ seed }}:' || {{ draw }} || ':' || {{ pick }}), 1, 8))::bit(32)::bigint,
        {{ dates }}
    )
{%- endmacro %}
