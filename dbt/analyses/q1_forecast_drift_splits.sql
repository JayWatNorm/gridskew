{{ config(group='analysis') }}

-- Q1's two secondary splits (decision 024), read in the same look as
-- q1_forecast_drift_verdict.sql and at the same analysis_read_until: the
-- reading rule's half-hours by time of day (four blocks by UTC start) and by
-- the source band on the poll that gave first_forecast. Each cell is
-- described with the verdict's own measures, not tested. Every listed cell
-- appears, with zero half-hours if it has none. Before the population is
-- reached it returns each cell's count and no drift.

{% set half_hours_needed = var('forecast_drift_half_hours_needed') %}
{% set read_until = analysis_read_until() %}

with half_hours as (
    select
        period_start,
        drift,
        first_intensity_index
    from {{ ref('int_carbon_error_by_period') }}
    where has_complete_trajectory
        and period_start >= timestamptz '{{ var("forecast_drift_population_from") }}'
{% if read_until is not none %}
        and period_start <= timestamptz '{{ read_until }}'
{% endif %}
),

population as (
    select
        count(*) >= {{ half_hours_needed }}
            and {{ forecast_look_ahead_reached(read_until) }} as is_reached
    from half_hours
),

cells as (
    select
        1 as section,
        'time of day (UTC start)' as split,
        case
            when extract(hour from period_start at time zone 'UTC') < 6 then '00:00-05:59'
            when extract(hour from period_start at time zone 'UTC') < 12 then '06:00-11:59'
            when extract(hour from period_start at time zone 'UTC') < 18 then '12:00-17:59'
            else '18:00-23:59'
        end as cell,
        drift
    from half_hours

    union all

    select
        2 as section,
        'first-forecast band' as split,
        coalesce(first_intensity_index, 'unclassified') as cell,
        drift
    from half_hours

    union all

    -- The listed cells, so an empty one still shows its zero.
    select
        section,
        split,
        cell,
        null as drift
    from (
        values
            (1, 'time of day (UTC start)', '00:00-05:59'),
            (1, 'time of day (UTC start)', '06:00-11:59'),
            (1, 'time of day (UTC start)', '12:00-17:59'),
            (1, 'time of day (UTC start)', '18:00-23:59'),
            (2, 'first-forecast band', 'very low'),
            (2, 'first-forecast band', 'low'),
            (2, 'first-forecast band', 'moderate'),
            (2, 'first-forecast band', 'high'),
            (2, 'first-forecast band', 'very high'),
            (2, 'first-forecast band', 'unclassified')
    ) as listed (section, split, cell)
)

select
    cells.section,
    cells.split,
    cells.cell,
    count(cells.drift) as half_hours,
    case when population.is_reached
        then percentile_cont(0.5) within group (order by cells.drift)
    end as median_drift,
    case when population.is_reached
        then round(
            (count(*) filter (where cells.drift > 0))::numeric
            / nullif(count(*) filter (where cells.drift <> 0), 0),
            6
        )
    end as positive_share
from cells
cross join population
group by cells.section, cells.split, cells.cell, population.is_reached
order by cells.section, cells.cell
