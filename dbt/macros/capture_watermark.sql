{% macro capture_watermark(relation) %}

    {#-
        The greatest last_captured_at in a period table, as a SQL literal.
        It is read before the model runs and written into the SQL, so the
        planner sees the real cutoff and uses the retrieved_at index. A
        subquery in the WHERE clause would be planned as "a third of the
        table" and scanned in full. An empty table gives -infinity, so the
        next run fills it from all history.
    -#}
    {%- if execute -%}
        {%- set result = run_query(
            "select coalesce(max(last_captured_at), '-infinity'::timestamptz)::text"
            ~ " from " ~ relation
        ) -%}
        '{{ result.columns[0].values()[0] }}'::timestamptz
    {%- else -%}
        '-infinity'::timestamptz
    {%- endif -%}

{% endmacro %}
