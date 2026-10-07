{{ config(group='analysis') }}

-- The one look at the forecast drift question
-- (docs/carbon-forecast-trajectory.md): does the carbon-intensity forecast
-- drift one way between its first and its final version? Run it once, on the
-- first day q1_forecast_drift_population reports 2,880 complete half-hours.
-- Before that day it returns the count and no result.

{% set half_hours_needed = 2880 %}

with half_hours as (
    select
        drift,
        has_complete_trajectory,
        as_published_drift,
        as_published_has_complete_trajectory
    from {{ ref('int_carbon_error_by_period') }}
    where period_start >= timestamptz '2026-08-17 00:00:00+00'
),

drifts as (
    select
        'reading_rule' as poll_set,
        drift
    from half_hours
    where has_complete_trajectory

    union all

    select
        'as_published' as poll_set,
        as_published_drift as drift
    from half_hours
    where as_published_has_complete_trajectory
),

poll_sets as (
    select 'reading_rule' as poll_set
    union all
    select 'as_published' as poll_set
),

statistics as (
    select
        poll_sets.poll_set,
        count(drifts.drift) as half_hours,
        percentile_cont(0.5) within group (order by drifts.drift) as median_drift,
        count(*) filter (where drifts.drift > 0) as positive_drifts,
        count(*) filter (where drifts.drift <> 0) as non_zero_drifts
    from poll_sets
    left join drifts
        on poll_sets.poll_set = drifts.poll_set
    group by poll_sets.poll_set
),

shares as (
    select
        poll_set,
        half_hours,
        median_drift,
        positive_drifts::numeric / nullif(non_zero_drifts, 0) as positive_share
    from statistics
),

verdicts as (
    select
        poll_set,
        half_hours,
        median_drift,
        positive_share,
        case
            when median_drift >= 2 and positive_share >= 0.55
                then 'supported: upward drift'
            when median_drift <= -2 and positive_share <= 0.45
                then 'contradicted: downward drift'
            else 'no directional drift'
        end as verdict
    from shares
),

reading_rule as (
    select
        half_hours,
        median_drift,
        positive_share,
        verdict,
        half_hours >= {{ half_hours_needed }} as population_is_reached
    from verdicts
    where poll_set = 'reading_rule'
),

as_published as (
    select
        half_hours,
        median_drift,
        positive_share,
        verdict
    from verdicts
    where poll_set = 'as_published'
)

select
    reading_rule.half_hours as complete_half_hours,
    {{ half_hours_needed }} as half_hours_needed,
    reading_rule.population_is_reached,
    case
        when not reading_rule.population_is_reached
            then 'population not reached: no result'
        when reading_rule.verdict <> as_published.verdict
            then 'not robust to frozen polls: no directional verdict'
        else reading_rule.verdict
    end as reported_verdict,
    case when reading_rule.population_is_reached
        then reading_rule.median_drift
    end as median_drift,
    case when reading_rule.population_is_reached
        then round(reading_rule.positive_share, 6)
    end as positive_share,
    case when reading_rule.population_is_reached
        then as_published.half_hours
    end as as_published_half_hours,
    case when reading_rule.population_is_reached
        then as_published.median_drift
    end as as_published_median_drift,
    case when reading_rule.population_is_reached
        then round(as_published.positive_share, 6)
    end as as_published_positive_share,
    case when reading_rule.population_is_reached
        then as_published.verdict
    end as as_published_verdict
from reading_rule
cross join as_published
