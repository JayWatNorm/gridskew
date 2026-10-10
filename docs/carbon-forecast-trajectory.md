# Carbon forecast trajectory

## In plain terms

NESO forecasts the carbon intensity of each half-hour about 48 hours ahead and
revises that forecast every 30 minutes until the half-hour arrives. GridSkew
keeps every revision. The models on this page line those revisions up for each
half-hour, from the first forecast to the final one.

The question: does the forecast drift one way between its first and its final
version?

The rule for reading the answer is fixed before any drift value is read. The
poll and trajectory thresholds are the `forecast_*` variables in
`dbt/dbt_project.yml`. The population minimum and the verdict thresholds are
in `dbt/analyses/q1_forecast_drift_verdict.sql`.

---

## Which polls count

`int_carbon_forecast__revisions` keeps every archived row and flags each poll.
A poll counts when all three hold:

- **It is the earliest poll of its half-hour slot.** The slot is
  `retrieved_at` rounded down to the half-hour. A retry or a re-run in the
  same slot is not a new forecast.
- **It is not partial.** A full poll returns at least 96 half-hours. A
  shorter window means the source feed has stopped moving.
- **It is not part of a no-change run.** A poll is unchanged when it repeats
  the previous slot's poll in every half-hour the two share. Four or more
  unchanged polls in a row are a frozen feed and none of them counts; partial
  polls count towards the four. A shorter repeat is normal and stays.

A slot with no poll does not end a run: the next poll is compared with the
last poll before the gap. A poll that shares no half-hour with the poll before
it is a new forecast.

## The trajectory of one half-hour

`int_carbon_forecast__by_period` has one row per forecast half-hour.

- **First forecast:** the counted poll with the largest horizon.
- **Final forecast:** the counted poll with the smallest horizon that is not
  negative. A poll made after the half-hour starts is never a forecast of it.
  Horizons are compared in exact seconds, so this holds to the fraction of a
  second.
- **Drift:** final forecast minus first forecast, in gCO2/kWh.
- **Complete trajectory:** at least 90 counted slots, a first horizon of at
  least 47 hours and a final horizon of at most half an hour. The poll that
  runs inside the half-hour counts as a slot.
- **As published.** The same trajectory with one poll per slot and no poll
  excluded sits beside it, so the answer can be checked against the archive
  as the source published it.

`int_carbon_outturn__latest` takes the latest capture with an observed value
for each half-hour. `int_carbon_error_by_period` joins the two: error is
actual minus forecast, so a positive error is a half-hour dirtier than
forecast.

## Reading the answer

- **Population:** half-hours from 17 August 2026 with an outturn and a
  complete trajectory; at least 2,880 of them (60 days' worth).
- **Verdict:** upward drift is supported when the median drift is at least +2
  and at least 55% of non-zero drifts are positive; contradicted when the
  median is at most −2 and at most 45% are positive; otherwise there is no
  directional drift.
- **Sensitivity check:** the same verdict as published. If the two differ, the
  result is "not robust to frozen polls" and no direction is reported.
- **One look.** `dbt/analyses/q1_forecast_drift_population.sql` counts the
  population without reading a forecast value and can run on any day.
  `dbt/analyses/q1_forecast_drift_verdict.sql` runs once, when the population
  reaches 2,880; before then it returns the count and no result.

## Where the models run

The four models are views in the private `analysis` group. No scheduled job
builds them. The nightly job runs their data tests, which return a count of
failing rows and no forecast value; the verdict is read once, by hand.
