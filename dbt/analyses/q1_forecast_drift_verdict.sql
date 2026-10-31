{{ config(group='analysis') }}

-- The one look at the forecast drift question
-- (docs/carbon-forecast-trajectory.md): does the carbon-intensity forecast
-- drift one way between its first and its final version? Run it once, on the
-- first day q1_forecast_drift_population reports
-- forecast_drift_half_hours_needed complete half-hours and at least three
-- slot polls are stored after analysis_read_until, so a forecast stall
-- running past the cutoff is settled; read it at least 8 days after the
-- cutoff, so outturn has settled. Before then it returns the count and date
-- range and no result.
--
-- Amended 31 October 2026, before the population reached 2,880 and before
-- any drift value was read. Beside the verdict, described, never changing or
-- withholding it:
-- - the verdict with each weekday weighted to its share of the calendar,
--   since stalls leave fewer complete half-hours on some weekdays. A
--   half-hour's weight is its UTC weekday's share of the half-hours from the
--   first to the last complete half-hour, over that weekday's share of the
--   population. The weighted median is the first drift, in ascending order,
--   at which the cumulative weight exceeds half the total; at exactly half,
--   the mean of that drift and the next. The weighted positive share is the
--   weight of positive drifts over the weight of non-zero drifts. The same
--   thresholds give the reweighted verdict; the as-published check is not
--   reweighted;
-- - the same verdict on the half-hours after forecast_explored_until
--   (10 September 2026 05:30 UTC), which an exploration of 11 September 2026
--   did not see, with n and no minimum of its own: the part not seen.
-- The mean drift with its interval and the correlation of successive
-- revisions are in q1_forecast_drift_beside.sql. Q1 shows the usual direction
-- of change, not whether the forecast is efficient.

{% set half_hours_needed = var('forecast_drift_half_hours_needed') %}
{% set read_until = analysis_read_until() %}

with half_hours as (
    select
        period_start,
        drift,
        has_complete_trajectory,
        as_published_drift,
        as_published_has_complete_trajectory
    from {{ ref('int_carbon_error_by_period') }}
    where period_start >= timestamptz '{{ var("forecast_drift_population_from") }}'
{% if read_until is not none %}
        and period_start <= timestamptz '{{ read_until }}'
{% endif %}
),

drifts as (
    select
        'reading_rule' as drift_set,
        period_start,
        drift
    from half_hours
    where has_complete_trajectory

    union all

    select
        'as_published' as drift_set,
        period_start,
        as_published_drift as drift
    from half_hours
    where as_published_has_complete_trajectory

    union all

    select
        'part_not_seen' as drift_set,
        period_start,
        drift
    from half_hours
    where has_complete_trajectory
        and period_start > timestamptz '{{ var("forecast_explored_until") }}'
),

drift_sets as (
    select 'reading_rule' as drift_set
    union all
    select 'as_published' as drift_set
    union all
    select 'part_not_seen' as drift_set
),

-- Each half-hour weighs 1 here; the weekday-reweighted statistics below
-- weigh each by its weekday.
statistics as (
    select
        drift_sets.drift_set,
        count(drifts.drift) as half_hours,
        min(drifts.period_start) as first_half_hour,
        max(drifts.period_start) as last_half_hour,
        percentile_cont(0.5) within group (order by drifts.drift) as median_drift,
        count(*) filter (where drifts.drift > 0)::numeric as positive_weight,
        count(*) filter (where drifts.drift <> 0)::numeric as non_zero_weight
    from drift_sets
    left join drifts
        on drift_sets.drift_set = drifts.drift_set
    group by drift_sets.drift_set
),

population as (
    select
        period_start,
        drift,
        extract(isodow from period_start at time zone 'UTC') as weekday
    from drifts
    where drift_set = 'reading_rule'
),

span_by_weekday as (
    select
        extract(isodow from span.period_start at time zone 'UTC') as weekday,
        count(*) as span_half_hours
    from statistics
    cross join generate_series(
        statistics.first_half_hour, statistics.last_half_hour, interval '30 minutes'
    ) as span (period_start)
    where statistics.drift_set = 'reading_rule'
    group by extract(isodow from span.period_start at time zone 'UTC')
),

population_by_weekday as (
    select
        weekday,
        count(*) as population_half_hours
    from population
    group by weekday
),

-- The product of the weekdays' population counts. Multiplying every weight
-- by it moves neither the median nor the share, and makes each weight a
-- whole number, so the half-weight test is exact.
population_count_product as (
    select
{%- for weekday in range(1, 8) %}
        coalesce(max(population_half_hours) filter (where weekday = {{ weekday }}), 1)::numeric
            {{- ' *' if not loop.last }}
{%- endfor %}
            as product
    from population_by_weekday
),

weighted_drifts as (
    select
        population.period_start,
        population.drift,
        span_by_weekday.span_half_hours * population_count_product.product
            / population_by_weekday.population_half_hours as weight
    from population
    inner join population_by_weekday
        on population.weekday = population_by_weekday.weekday
    inner join span_by_weekday
        on population.weekday = span_by_weekday.weekday
    cross join population_count_product
),

cumulative_weights as (
    select
        drift,
        weight,
        sum(weight) over (order by drift, period_start rows unbounded preceding)
            as cumulative_weight,
        sum(weight) over () as total_weight
    from weighted_drifts
),

-- In ascending order: the drift at which the cumulative weight is exactly
-- half the total, if any, and the first drift past half.
weighted_median_bounds as (
    select
        min(drift) filter (where 2 * cumulative_weight = total_weight) as half_weight_drift,
        min(drift) filter (where 2 * cumulative_weight > total_weight) as past_half_weight_drift,
        coalesce(sum(weight) filter (where drift > 0), 0) as positive_weight,
        coalesce(sum(weight) filter (where drift <> 0), 0) as non_zero_weight,
        count(*) as half_hours
    from cumulative_weights
),

weighted_statistics as (
    select
        'weekday_reweighted' as drift_set,
        half_hours,
        null::timestamptz as first_half_hour,
        null::timestamptz as last_half_hour,
        case
            when half_weight_drift is not null
                then (half_weight_drift + past_half_weight_drift) / 2.0
            else past_half_weight_drift
        end::double precision as median_drift,
        positive_weight,
        non_zero_weight
    from weighted_median_bounds
),

all_statistics as (
    select
        drift_set,
        half_hours,
        first_half_hour,
        last_half_hour,
        median_drift,
        positive_weight,
        non_zero_weight
    from statistics

    union all

    select
        drift_set,
        half_hours,
        first_half_hour,
        last_half_hour,
        median_drift,
        positive_weight,
        non_zero_weight
    from weighted_statistics
),

-- One verdict for every drift set. The share tests compare exact weights,
-- never a rounded share.
verdicts as (
    select
        drift_set,
        half_hours,
        first_half_hour,
        last_half_hour,
        median_drift,
        positive_weight / nullif(non_zero_weight, 0) as positive_share,
        case
            when median_drift >= 2
                and positive_weight >= 0.55 * nullif(non_zero_weight, 0)
                then 'supported: upward drift'
            when median_drift <= -2
                and positive_weight <= 0.45 * nullif(non_zero_weight, 0)
                then 'contradicted: downward drift'
            else 'no directional drift'
        end as verdict
    from all_statistics
),

reading_rule as (
    select
        half_hours,
        first_half_hour,
        last_half_hour,
        median_drift,
        positive_share,
        verdict,
        half_hours >= {{ half_hours_needed }}
            and {{ forecast_look_ahead_reached(read_until) }} as population_is_reached
    from verdicts
    where drift_set = 'reading_rule'
),

as_published as (
    select
        half_hours,
        median_drift,
        positive_share,
        verdict
    from verdicts
    where drift_set = 'as_published'
),

weekday_reweighted as (
    select
        median_drift,
        positive_share,
        verdict
    from verdicts
    where drift_set = 'weekday_reweighted'
),

part_not_seen as (
    select
        half_hours,
        median_drift,
        positive_share,
        verdict
    from verdicts
    where drift_set = 'part_not_seen'
)

select
    reading_rule.half_hours as complete_half_hours,
    reading_rule.first_half_hour,
    reading_rule.last_half_hour,
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
    end as as_published_verdict,
    case when reading_rule.population_is_reached
        then weekday_reweighted.median_drift
    end as weekday_reweighted_median_drift,
    case when reading_rule.population_is_reached
        then round(weekday_reweighted.positive_share, 6)
    end as weekday_reweighted_positive_share,
    case when reading_rule.population_is_reached
        then weekday_reweighted.verdict
    end as weekday_reweighted_verdict,
    part_not_seen.half_hours as part_not_seen_half_hours,
    case when reading_rule.population_is_reached
        then part_not_seen.median_drift
    end as part_not_seen_median_drift,
    case when reading_rule.population_is_reached
        then round(part_not_seen.positive_share, 6)
    end as part_not_seen_positive_share,
    case when reading_rule.population_is_reached
        then part_not_seen.verdict
    end as part_not_seen_verdict
from reading_rule
cross join as_published
cross join weekday_reweighted
cross join part_not_seen
