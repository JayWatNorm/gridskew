{% macro settlement_period_count(settlement_date) %}

    (
        extract(
            epoch from (
                (
                    (({{ settlement_date }})::date + 1)::timestamp
                    at time zone 'Europe/London'
                )
                -
                (
                    ({{ settlement_date }})::date::timestamp
                    at time zone 'Europe/London'
                )
            )
        ) / 1800
    )::integer

{% endmacro %}

{% macro settlement_period_start_utc(settlement_date, settlement_period) %}

    case
        when {{ settlement_date }} is null
            or {{ settlement_period }} is null
            then null::timestamptz

        when {{ settlement_period }} between 1
            and {{ settlement_period_count(settlement_date) }}
            then
                ({{ settlement_date }})::date::timestamp
                    at time zone 'Europe/London'
                + ({{ settlement_period }} - 1)
                    * interval '30 minutes'

        else null::timestamptz
    end

{% endmacro %}