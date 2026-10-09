{#
    Keep one row for each node of a dbt invocation.

    The project's on-run-end hook calls record_run_results(results). It
    returns SQL only when the invocation passes --vars '{audit: true}';
    every other invocation gets an empty hook and writes nothing.

    The table is in the target schema. It is created when missing, and each
    audited invocation deletes rows older than node_results_retention_days.
    No view reads it, so no scheduled job replaces a relation by writing it.

    A hook sees one invocation. GRIDSKEW_AIRFLOW_RUN_ID, set by the DAG that
    starts dbt, ties the invocations of one Airflow run together; it is null
    for a run from a workstation.
#}

{% macro node_results_relation() %}
    {{- target.schema }}.dbt_node_results
{%- endmacro %}

{% macro sql_text_or_null(value) %}
    {%- if value is none or (value | string | trim) == '' -%}
        null
    {%- else -%}
        '{{ dbt.escape_single_quotes(value | string) }}'
    {%- endif -%}
{% endmacro %}

{% macro sql_number_or_null(value) %}
    {%- if value is number -%}
        {{ value }}
    {%- else -%}
        null
    {%- endif -%}
{% endmacro %}

{% macro node_result_values(result) %}
    (
        {{ sql_text_or_null(invocation_id) }},
        {{ sql_text_or_null(env_var('GRIDSKEW_AIRFLOW_RUN_ID', '')) }},
        {{ sql_text_or_null(flags.WHICH) }},
        {{ sql_text_or_null(result.node.unique_id) }},
        {{ sql_text_or_null(result.node.resource_type) }},
        {{ sql_text_or_null(result.status) }},
        {{ sql_number_or_null(result.execution_time) }},
        {{ sql_number_or_null(result.adapter_response.get('rows_affected')) }},
        {{ sql_number_or_null(result.failures) }},
        {{ sql_text_or_null(result.message) }}
    )
{% endmacro %}

{% macro record_run_results(results) %}
    {%- if not execute or not var('audit', false) or results | length == 0 -%}
        {{ return('') }}
    {%- endif -%}

    {%- set retention_days = var('node_results_retention_days') | int -%}
    {%- if retention_days < 1 -%}
        {{ exceptions.raise_compiler_error(
            "node_results_retention_days must be a whole number of days, 1 or "
            ~ "more; got '" ~ var('node_results_retention_days') ~ "'"
        ) }}
    {%- endif -%}

    create table if not exists {{ node_results_relation() }} (
        invocation_id       text        not null,
        airflow_run_id      text,
        dbt_command         text        not null,
        node_id             text        not null,
        resource_type       text        not null,
        status              text        not null,
        execution_seconds   numeric,
        rows_affected       bigint,
        failures            integer,
        message             text,
        recorded_at         timestamptz not null default now(),
        primary key (invocation_id, node_id)
    );

    delete from {{ node_results_relation() }}
    where recorded_at < now() - interval '{{ retention_days }} days';

    insert into {{ node_results_relation() }} (
        invocation_id,
        airflow_run_id,
        dbt_command,
        node_id,
        resource_type,
        status,
        execution_seconds,
        rows_affected,
        failures,
        message
    )
    values
    {%- for result in results %}
        {{ node_result_values(result) }}{{ "," if not loop.last }}
    {%- endfor %};
{% endmacro %}
