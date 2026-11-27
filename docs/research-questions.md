# Research questions

GridSkew answers these questions with public GB electricity data. Each
question names the data that answers it, the analysis that reads it and its
method: what is measured, on which population, and how the result is
summarised. This page holds the questions and their methods, not the
results. When each method was written is under
[How the questions were set up](#how-the-questions-were-set-up).

## Status

One row per question, with the date of its read. Each row links to its
method.

| ID | Question, in short | Status |
|---|---|---|
| [Q1](#q1-does-the-carbon-intensity-forecast-drift-one-way-between-its-first-and-final-version) | Carbon forecast drift, first to final version | read (22 November 2026) |
| [Q2](#q2-how-much-does-metered-output-change-between-settlement-runs) | Metered output between settlement runs | read (11 October 2026) |
| [Q3](#q3-is-metered-output-usually-above-or-below-the-notified-position-pn-by-fuel-group) | Metered output against PN, by fuel group | read (11 October 2026) |
| [Q5](#q5-how-much-positive-unexplained-shortfall-coincides-with-outages-published-in-remit-at-the-period-start) | Shortfall with a published outage | read (12 October 2026) |
| [Q6](#q6-how-long-after-its-written-start-is-an-outage-notice-first-published) | Outage notice publication lag | read (12 October 2026) |
| [Q8](#q8-is-demand-forecast-error-more-strongly-associated-with-carbon-forecast-error-than-shortfall-is) | Demand forecast error or shortfall, which goes with carbon error more | read (26 October 2026) |
| [Q10](#q10-how-accurate-is-the-forecast-at-each-lead-time) | Forecast accuracy by lead time | read (22 November 2026) |
| [Q12](#q12-how-much-of-the-deviation-from-pn-was-instructed-boalf-rather-than-unexplained) | Instructed against unexplained deviation | read (11 October 2026) |
| [Q22](#q22-did-later-meter-data-change-one-weeks-fuel-group-picture) | One week's fuel-group picture, first against latest run | read (11 October 2026) |
| [Q23](#q23-how-close-to-their-pns-do-units-run-when-not-instructed) | PN accuracy when not instructed | read (15 October 2026) |
| [Q24](#q24-what-share-of-each-fuel-groups-instructed-energy-is-flagged-as-a-system-action-rather-than-energy-balancing) | Instructed energy flagged as system actions | read (15 October 2026) |
| [Q25](#q25-how-fragmented-are-instructions-to-units-typed-other-mostly-batteries-and-pumped-storage-compared-with-thermal-plant) | Instruction fragmentation by fuel group | read (15 October 2026; re-run 25 October 2026 over the same period) |
| [Q28](#q28-are-half-hours-with-a-larger-error-in-the-apis-stored-carbon-forecast-associated-with-more-unexplained-shortfall-from-august-2025-to-august-2026) | Stored forecast error against unexplained shortfall, a year of history | read (26 October 2026) |
| [Q29](#q29-how-does-the-error-of-the-apis-stored-carbon-forecast-vary-by-month-and-by-time-of-day-over-a-year-of-history) | Stored forecast error by month and time of day | read (27 October 2026) |
| [Q30](#q30-is-the-size-of-the-miss-between-wind-outturn-and-nesos-latest-wind-forecast-associated-with-the-size-of-the-error-in-the-apis-stored-carbon-forecast-from-august-2025-to-august-2026) | Wind forecast miss against stored carbon forecast error | read (27 October 2026) |
| [Q37](#q37-when-neso-turns-wind-farms-down-what-does-it-turn-up-in-the-same-half-hours) | Wind turned down, and what is turned up in the same half-hours | read (27 November 2026) |

Questions dropped before a method was written are under
[Retired](#retired).

## How questions are read

- **Each result is read at a fixed cutoff** (`analysis_read_until`).
- **Every number has its context:** n, the date range and coverage beside it.
- **Associations, not causes.** A result says "associated with", never
  "caused by".
- **A result that shows no link or no difference is a result** and is
  published.
- **REMIT notices.** The current version of a notice at a given time is its
  publication with the latest `publish_time` not after that time; ties go to
  the later `created_time`, then the higher `revision_number`. Its first
  publication is the one in force at its earliest `publish_time`: when
  several share that time, the one with the later `created_time`, then the
  higher `revision_number`. Revision numbers
  are the participant's own counter: they repeat and can fall.
  [Design decisions](design-decisions.md#modelling)
- **Instructions.** A later acceptance (higher acceptance number) replaces
  an earlier one only for the minutes they share, and the PN applies where
  no acceptance does. Each ramp point is read from its latest capture.
  Instructed energy is never added up across the acceptances of one unit.
  [Design decisions](design-decisions.md#modelling)

**The main question.** GridSkew asks whether half-hours in which power
stations miss their own plans are also half-hours in which NESO's carbon
forecast misses by more. NESO builds its forecast from its own demand, wind
and solar forecasts, not from those plans, so any link is indirect. Q1
describes how the forecast is revised, and Q10 and Q29 how far it misses.
Q3, Q5, Q12 and Q23 describe delivery against plan; Q28 looks at its link
to the forecast's miss; Q8 and Q30 look at competing or contextual
explanations.

### Terms

- **BM unit:** a unit registered in the Balancing Mechanism: a generator,
  battery, pumped-storage station, interconnector link or supplier
  account.
- **PN (Physical Notification):** the output a BM unit tells NESO it plans
  to produce, half-hour by half-hour; the latest capture is used.
- **B1610:** Elexon's metered output per BM unit and settlement period,
  recalculated in settlement runs as later meter data arrive.
- **BOALF (Bid Offer Acceptance Level Flagged):** Elexon's record of each
  bid or offer NESO accepts (an acceptance), with its levels and flags.
- **Cohort:** every registry BM unit with a published fuel type, grouped
  into fuel groups.
- **Determinable:** a unit-period whose shortfall can be judged: it has one
  metered value and an expected energy, which needs a usable PN unless an
  acceptance covers the whole half-hour. The others keep their row with the
  reason.
- **Unexplained shortfall** (`unexplained_shortfall_mwh`): expected energy
  minus the latest settlement run's metered energy; in plain words, the
  shortfall NESO did not instruct. Expected energy is the PN, with the
  instructed level in its place for the minutes an acceptance is in force.
  Its positive part is under-delivery.
- **Deviation from PN** (`deviation_from_pn_mwh`): PN energy minus metered
  energy, which is the instructed deviation (`instructed_deviation_mwh`, PN
  energy minus expected energy) plus the unexplained shortfall. Positive
  means the unit produced less than its PN.
- **ABSVD:** balancing services settled outside the balancing mechanism.
- **Slot poll:** the earliest forecast poll in a half-hour slot of its
  retrieval time.
- **Carbon forecast error:** actual minus forecast, in gCO2/kWh, so a
  positive error is a half-hour dirtier than forecast. The actual is NESO's
  own estimate.
- **`error_api_final`:** the error of the stored forecast, the one value
  NESO's API keeps for each past half-hour; where GridSkew's archive could
  check it (17 August to 10 September 2026), it matched a late poll of the
  archive, in a check whose output is not published. The archive starts on 17 August
  2026, so the year before it has only this value.

### Forecast read controls

A forecast result is read only after the forecast feed has run past the
cutoff and the outturn has had eight days to settle. The query, output
and read time are saved; limiting a rerun to the data held then is done by
hand. For Q1 and Q10:

- read only when at least three slot polls are stored after
  `analysis_read_until` (one fewer than the run of four that marks a frozen
  feed, so a stall running past the cutoff is settled);
- read at least 8 days after the cutoff, so outturn has settled;
- the cutoff carries an explicit UTC offset;
- the read records its time and saves the compiled query and output; a
  rerun is limited by hand to outturn and forecast captures with
  `retrieved_at` up to that time.

## Questions designed in

### Q1. Does the carbon-intensity forecast drift one way between its first and final version?

**Data:** the forecast archive, every version polled half-hourly.
**Analysis:**
[q1_forecast_drift_population.sql](../dbt/analyses/q1_forecast_drift_population.sql),
[q1_forecast_drift_verdict.sql](../dbt/analyses/q1_forecast_drift_verdict.sql),
[q1_forecast_drift_splits.sql](../dbt/analyses/q1_forecast_drift_splits.sql),
[q1_forecast_drift_beside.sql](../dbt/analyses/q1_forecast_drift_beside.sql);
the build is described in
[Carbon forecast trajectory](carbon-forecast-trajectory.md).

**Status:** read on 22 November 2026, 08:20 UTC, covering half-hours up to
the one starting at 23:30 UTC on 12 November 2026. The population reached
2,880 complete half-hours on 12 November 2026.

**Earlier look.** On 11 September 2026 an exploration compared, for 851
half-hours from 19 August to 10 September 2026, the forecasts made 36 to
48 hours ahead with those made 0 to 6 hours ahead. In this method's terms
the average change was −0.18 gCO2/kWh (95% interval −1.60 to +1.17), the
middle change −1.65, and 54.8% of half-hours moved down. The exploration
covered 380 of this population's half-hours (19 August to 4 September
2026). The thresholds below were set on 5 October 2026, after this
exploration; whether they were set knowing it is not recorded. A
published analysis of the same forecast (gridcast), using revisions from
20 August 2026 that overlap this population, finds successive revisions
negatively correlated.

**Method:**

- **Polls.** The earliest poll in each half-hour slot of `retrieved_at`
  counts; a later poll in the same slot is never compared or counted.
  Excluded: partial polls (fewer than 96 half-hours) and no-change polls. A
  poll is unchanged when it shares at least one half-hour with the previous
  slot's poll and every shared value is equal; it is excluded when it is one
  of a run of four or more unchanged polls in a row. Partial polls count
  towards the four. The previous slot's poll is the earliest poll of the
  nearest earlier slot that holds one, so a slot with no poll does not end a
  run.
- **Trajectory.** The horizon is the half-hour's start minus `retrieved_at`,
  compared in exact seconds. The first forecast is the counted poll with the
  largest horizon; the final forecast is the counted poll with the smallest
  horizon that is not negative. A trajectory is complete with at least 90
  counted slots, a first horizon of at least 47 hours and a final horizon of
  at most 0.5 hours. The poll made inside the half-hour counts as a slot and
  is never the final forecast.
- **Population.** Half-hours from 17 August 2026 with an outturn and a
  complete trajectory; at least 2,880 of them.
- **Measure.** Drift = final forecast − first forecast, in gCO2/kWh.
- **Answer.** Drift is called upward when the median drift is at least +2
  and the share of positive drifts, zeros excluded, is at least 55%;
  downward when the median is at most −2 and the positive share at most
  45%; otherwise there is no directional drift. The saved output writes
  these as `supported: upward drift`, `contradicted: downward drift` and
  `no directional drift`, in columns named verdict. The share is compared
  exactly; the reported share is rounded to six decimals.
- **Sensitivity check.** The same answer, recomputed with one poll per
  slot, no poll excluded, the same thresholds and no population minimum of
  its own. If the two answers differ, the result is "not robust to frozen
  polls" and no direction is reported.
- **Beside the answer.** The date range, the complete half-hours per
  weekday and per half-hour of the day (UTC), and the half-hours removed by
  each exclusion, counted in order: partial polls first, then no-change
  polls.
- **Secondary splits, described only.** Each cell reports n, the median
  drift and the positive share (positive drifts divided by non-zero
  drifts). (1) Time of day, in four blocks by each half-hour's UTC start:
  00:00-05:59, 06:00-11:59, 12:00-17:59, 18:00-23:59. (2) The source's
  `intensity_index` band (`very low`, `low`, `moderate`, `high`,
  `very high`) on the poll that gave the first forecast; a poll with no
  band is counted as unclassified, never replaced by a later band. There
  is no season split, as the data spans mid-August to November, and no
  daily statistic.
- **Also beside the answer, described.** The mean drift with a 90%
  day-block bootstrap interval; the correlation between successive changes
  in a half-hour's forecast; the answer with each weekday weighted to its
  share of the calendar, since stalls leave fewer complete half-hours on
  some weekdays; and the same answer on the half-hours after 10 September
  2026 05:30 UTC, which the exploration did not see. None changes the
  answer. The first two were added on 31 October 2026, after the
  exploration and the published analysis above had looked at related
  figures.
- **Read.** With `analysis_read_until` on the first day the population
  reaches 2,880, under the
  [forecast read controls](#forecast-read-controls). Before then the query
  returns the count and no result.
- The answer shows the usual direction of revision, not whether the
  forecast is efficient.

**For scale** (added 6 November 2026; not part of the method). Q1's ±2 g/kWh
threshold is about a fifth of the stored forecast's mean miss size against NESO's actual
from 22 August 2025 to 16 August 2026 (Q29: 9.3 g/kWh), a different measure
in the same units; the comparison is illustrative only.

### Q2. How much does metered output change between settlement runs?

**Data:** B1610 metered output, interim (II), initial (SF) and first
reconciliation (R1) runs.
**Analysis:**
[q2_settlement_run_changes.sql](results/q2_settlement_run_changes.sql), the
query as run by hand, kept beside its saved outputs.

**Status:** read on 11 October 2026.

**Method:**

- **Population.** Settlement dates 10 to 16 August 2026, every BM unit;
  two run pairs, II to SF and SF to R1, over matched unit-periods.
- **Measures, per run pair.** Moved share = Σ|later − earlier| ÷
  Σ|earlier|; changed share = unit-periods whose quantity changed ÷ matched
  unit-periods.
- **Labels.** Moved share: small below 0.5%, material at 2% or more.
  Changed share: small below 5%, material at 20% or more. A value between
  the limits is intermediate. A pair is material when either measure is
  material, small when both are small, otherwise intermediate. Labels use
  the exact shares.
- **What it decides, on SF to R1 only.** Both small: R2 and R3 are not
  captured and RF alone is enough. Either material: a bounded R2 capture is
  proposed. Otherwise: reported as intermediate, no action. II to SF is
  reported beside it and does not enter the decision.

### Q3. Is metered output usually above or below the notified position (PN), by fuel group?

**Data:** PN and B1610.
**Analysis:**
[q3_metered_vs_notified.sql](../dbt/analyses/q3_metered_vs_notified.sql),
with coverage in [q3_coverage.sql](../dbt/analyses/q3_coverage.sql).

**Status:** read on 11 October 2026.

**Method:** describe only: the median and interquartile range of
`deviation_from_pn_mwh` per fuel group, with coverage beside it. The
analysis reads determinable cohort periods with a PN. The median is signed,
so it shows direction, not distance. This is not NESO's PN-accuracy measure
(see Q23).

### Q5. How much positive unexplained shortfall coincides with outages published in REMIT at the period start?

**Data:** REMIT publications and the cohort's unexplained shortfall.
**Analysis:**
[q5_shortfall_with_known_outage.sql](../dbt/analyses/q5_shortfall_with_known_outage.sql)

**Status:** read on 12 October 2026, 21:19 UTC, covering half-hours up to
the one starting at 22:30 UTC on 27 September 2026.

**Method:**

- **Measure.** The share of unexplained shortfall MWh in half-hours with a
  known outage, split planned and unplanned; a measured share, no answer.
  The shortfall is the positive part of `unexplained_shortfall_mwh`
  (under-delivery).
- **Known outage.** A half-hour has one when a REMIT production
  unavailability publication in force at the half-hour's start (the
  notice's current version at that time), with status `Active`, names the
  unit by that publication's own mapping (its `asset_id`, else its
  `affected_unit`) and its event overlaps any part of the half-hour. Any
  outage size counts.
- **Classes.** `planned`, `unplanned`, `both` (both in force) and `none`,
  which add to 100%.
- **Population.** Every determinable cohort unit-period, with or without a
  usable PN (one without is determinable only when an acceptance covers the
  whole half-hour); coverage of what is left out reported beside it.
- **Read.** With `analysis_read_until` = 2026-09-27 22:30 UTC (the
  last complete II settlement date), after a counts-only check that the
  REMIT views answer in time on a quiet server; the unedited output saved.
- "Known" means published in REMIT at the half-hour's start, not that the
  generator knew. An association, not a cause.

### Q6. How long after its written start is an outage notice first published?

**Data:** REMIT `publish_time` against `event_start_time`.
**Analysis:**
[q6_publication_lag.sql](../dbt/analyses/q6_publication_lag.sql)

**Status:** read on 12 October 2026.

**Method:**

- **Measure.** The median and 90th-percentile lag by outage class.
- **Lag.** The first publication's `publish_time` minus its event start.
  The class is the first publication's. The event start is the one the
  participant wrote, so the lag is not the time from failure to
  publication. Who stamps `publish_time` is not documented.
- **Population.** Notices whose current status is not `Dismissed`; first
  stored publication on or after 1 November 2024 and event start on or
  after 22 August 2024. All four unavailability event types are in the
  headline, split by type beside it; other market information is excluded.
  Every exclusion is counted in the coverage section.
- REMIT's "without delay" duty is not judged from this: the data cannot
  show when the participant knew. In Great Britain REMIT is assimilated law
  derived from Regulation (EU) 1227/2011, enforced by Ofgem. ACER's
  one-hour guidance is EU guidance; it and Regulation 543/2013 Article 15
  are context, not a compliance test.
- "Dismissed" means dismissed as of the read, so the result cannot be
  regenerated exactly later.

### Q8. Is demand forecast error more strongly associated with carbon forecast error than shortfall is?

**Data:** national demand forecast (NDF) and Initial National Demand
Outturn (INDO) issues for a year of history, the carbon outturn with the
API's stored forecast, and the cohort's unexplained shortfall.
**Analysis:** [q8_q28_history.sql](../dbt/analyses/q8_q28_history.sql),
shared with Q28.

**Status:** read on 26 October 2026, 11:21 UTC, after a counts-only
coverage check of the demand data the same day.

**Method:**

- **Window.** Settlement dates 22 August 2025 to 16 August 2026, read with
  Q28 on one shared population.
- **Carbon error.** `error_api_final`: the error of the stored forecast
  (see [Terms](#terms)); the only carbon forecast held for that year.
- **Demand error.** The first INDO outturn issued for the half-hour minus
  the last NDF issued before the half-hour starts, national boundary.
- **Shortfall.** Per half-hour, the cohort sum of the positive part of
  `unexplained_shortfall_mwh`, as Q5.
- **Population.** Matched days with all three present for at least 46
  half-hours; every half-hour on them with all three.
- **Measure.** Spearman `rho_demand` (absolute carbon error with absolute
  demand error) and `rho_shortfall` (absolute carbon error with the
  shortfall), each with n; gap = `rho_demand` − `rho_shortfall`.
- **Answer.** Demand is called stronger at a gap of 0.1 or more, shortfall
  at −0.1 or less; otherwise there is no clear difference.
- **Interval.** A 90% day-block bootstrap percentile interval for the gap
  (1,000 draws, both correlations on the same resampled half-hours) is
  descriptive and does not move the answer. A draw with a constant measure
  forms no gap; with fewer than 900 valid draws the interval is
  unavailable.
- **Read.** After the demand back-fill and its counts-only coverage
  check.
- **Half-hour alignment (added 7 November 2026, after the read).** This
  result pairs each carbon value with the Elexon half-hour of the same API
  label, as published. Against NESO's historic generation mix, whose wind
  equals Elexon's at the same half-hour in 99.1% of 17,280 half-hours, the
  API's actual is within 5 g/kWh in 45.8% of half-hours one half-hour after
  its label, against 35.8% at its own label (correlation of half-hour
  changes 0.61, against 0.43). This is a measured alignment, not a
  documented convention. The result stands as read; no claim is made about
  how the alignment bears on it.

### Q10. How accurate is the forecast at each lead time?

**Data:** the forecast archive and outturn.
**Analysis:**
[q10_forecast_accuracy_by_lead_time.sql](../dbt/analyses/q10_forecast_accuracy_by_lead_time.sql)

**Status:** read on 22 November 2026, 08:26 UTC, after Q1's read, covering
half-hours up to the one starting at 23:30 UTC on 12 November 2026.

**Earlier look.** On 11 September 2026 an exploration compared every
stored forecast with outturn for the half-hours from 17 August 21:00 to
10 September 05:30 UTC, pooled into lead-time bands. It overlaps the start
of this population (365 half-hours, 19 August to 4 September 2026). In
this method's sign (actual minus forecast) the mean error was +1.61 at 0-6
hours, +2.29 at 6-12, +0.05 at 12-24, −2.97 at 24-36 and −6.42 at 36-48
hours (medians +2, +2, −2, −5, −8); on forecasts that covered the full 48
hours, +1.43, +3.43, +2.23, −3.15 and −7.37 (medians +1, +3, +2, −5, −8).
The root-mean-square error ran from 31.5 to 38.0 across all forecasts and
from 28.1 to 37.8 on full-window forecasts; between 46% and 62% of
forecasts were above outturn. The 24-hour forecast falls in the 12-24 hour
band (24-36 hours when an earlier poll stands in); the 4-hour and
half-hour forecasts in the 0-6 hour band. For the API's own final
forecast, three figures were also seen: a mean of −0.43 (median 0) from
18 August 2025 to 10 September 2026; a mean of about +2.0 over 1,311
half-hours that include 16 August to 10 September 2026; and, derived from a
published third-party score and Q29's monthly results, a mean absolute
error of about 13.7 from 17 August to 28 September 2026. No mean absolute
error of the forecasts this method reads was computed.

**Method:**

- **Measure.** Accuracy by lead time, not "skill" (which means against a
  reference forecast): the mean absolute error and bias of the forecast
  about 24 hours, about 4 hours and about half an hour ahead, against
  outturn.
- **Horizons.** The 24-hour and 4-hour forecasts are the counted poll
  nearest that horizon, and null when none is within half an hour of it;
  the half-hour forecast is Q1's final. Until 10 September 2026 10:30 UTC
  the archive polled on the half-hour, so the three were made about a
  second short of 24 hours, 4 hours and 30 minutes ahead. Since then it
  polls at :05 and :35, so they are made about 23 hours 55 minutes, 3
  hours 55 minutes and 25 minutes ahead, or 30 minutes earlier when that
  poll is missing or not counted. The shortest, median and longest lead
  time of each, and the number of earlier polls used, are reported.
- **Sample.** One common sample: Q1's half-hours that have all three
  horizons, with each horizon's n and the half-hours dropped reported.
- **Interval.** A 90% day-block bootstrap interval (1,000 draws) for each
  figure. It is descriptive.
- **Context, not a test.** On the same half-hours: persistence (the
  outturn of the latest half-hour that had ended when each forecast was
  made) and the same half-hour the day before. Both use outturn as later
  stored. At the 24-hour lead time the day-before half-hour ends up to an
  hour after the forecast is made, and the table says so.
- **Consistency check.** The API's own final forecast on the same
  half-hours. Its timing is not documented.
- **Part not seen.** The same figures on the half-hours after 10 September
  2026 05:30 UTC, which the exploration did not see, beside the result;
  they never change it.
- **Split.** By settlement-date type: weekday, weekend and bank holiday
  (England and Wales), described with n; no comparison is made for bank
  holidays (too few).
- **Read.** After Q1's read, at the same recorded UTC cutoff, under the
  [forecast read controls](#forecast-read-controls).

### Q12. How much of the deviation from PN was instructed (BOALF) rather than unexplained?

**Data:** BOALF, PN and B1610.
**Analysis:**
[q12_instructed_vs_residual.sql](../dbt/analyses/q12_instructed_vs_residual.sql)

**Status:** read on 11 October 2026.

**Method:**

- **Measure.** Describe, per fuel group, over every determinable cohort
  period with a PN, the instructed part as a share of the components'
  sizes, not of each period's own deviation: Σ|`instructed_deviation_mwh`|
  ÷ (Σ|`instructed_deviation_mwh`| + Σ|`unexplained_shortfall_mwh`|), with
  n, instructed periods and the three absolute sums beside it.
- ABSVD is not counted.
- **At the read.** Overlapping acceptances were ordered by acceptance time,
  then acceptance number. The model orders them by acceptance number, the
  balancing mechanism's own order, so a rebuild gives different values; the
  two orders differ for 32 of 3,620,900 acceptances.

### Q22. Did later meter data change one week's fuel-group picture?

**Data:** B1610 runs and the Q3 facts for Q2's settlement dates.
**Analysis:**
[q22_first_against_latest_run.sql](results/q22_first_against_latest_run.sql),
the query as run by hand, kept beside its saved output.

**Status:** read on 11 October 2026.

**Method:**

- **Measure.** The Q3 statistics (median and interquartile range of
  `deviation_from_pn_mwh` per fuel group) on the first run and the latest
  run of settlement dates 10 to 16 August 2026, side by side.
- **Answer.** The headline is called changed when the order of the fuel
  groups by median deviation changes, or when any group's median changes
  sign. No numeric threshold. Unit-periods need a first-run value.
- **Ties (added 25 November 2026, after the read).** Coal, hydro,
  interconnector and pumped storage have a median of 0 on both runs; tied
  medians have no order between them. The other groups keep their order,
  and no median changes sign.
- **At the read.** "Latest" was R1, so the read compares II with R1. There
  is no re-read.

The comparison uses medians, so it cannot show large revisions at a few
units.

### Q23. How close to their PNs do units run when not instructed?

A proxy for NESO's PN-accuracy measure.

**Data:** PN, B1610, BOALF and registry capacity.
**Analysis:**
[q23_pn_accuracy_proxy.sql](../dbt/analyses/q23_pn_accuracy_proxy.sql)

**Status:** read on 15 October 2026, 20:05 UTC, covering half-hours up to
the one starting at 22:30 UTC on 30 September 2026.

**Method:**

- **Population.** Per cohort unit and calendar month, determinable
  half-hours with no acceptance in force for any minute of the half-hour
  (`instruction_status = 'uninstructed'`); NESO likewise excludes any
  half-hour with a bid-offer acceptance. Interconnector units are excluded,
  as NESO's monitoring excludes them. Units with no registry capacity are
  left out and counted. Idle units count; their share is not reported.
- **Measures.** Net % = Σ(PN − metered) ÷ Σ(capacity × 0.5 h); absolute
  % = Σ|PN − metered| ÷ Σ(capacity × 0.5 h).
- **Reported.** The median and 90th percentile per fuel group and month.
- **Declared split.** Days with SO-flagged wind bids against days without.
- **A proxy.** ABSVD and dynamic MEL/MIL are not used, so NESO's thresholds
  are not applied and no unit is called inaccurate.

### Q24. What share of each fuel group's instructed energy is flagged as a system action rather than energy balancing?

**Data:** BOALF flags and instruction intervals.
**Analysis:**
[q24_system_action_share.sql](../dbt/analyses/q24_system_action_share.sql)

**Status:** read on 15 October 2026, 20:10 UTC, covering half-hours up to
the one starting at 22:30 UTC on 30 September 2026.

**Earlier look.** An exploration read level-energy shares on 11 October
2026. The measure was changed to acceptance volume on 12 October 2026,
after that first look at the data.

**Method:**

- **Measure.** Per fuel group and month, the acceptance volume flagged as
  a system action, as a share of all acceptance volume. Acceptance volume
  is the absolute difference between the instructed level and the PN over
  the minutes each acceptance is in force, as settlement measures it.
- **Beside it.** The level-energy share (the same share on the energy at
  the instructed level, not its difference from PN; the exploration's
  measure and the first one written) is reported for continuity.
- **Wording.** "Flagged as system actions", not "constraint volume": the
  flag is NESO's real-time classification, and later corrections are not
  re-read.

### Q25. How fragmented are instructions to units typed other (mostly batteries) and pumped storage compared with thermal plant?

**Data:** BOALF.
**Analysis:**
[q25_instruction_fragmentation.sql](../dbt/analyses/q25_instruction_fragmentation.sql)

**Status:** read on 15 October 2026, 22:01 UTC, covering half-hours up to
the one starting at 22:30 UTC on 30 September 2026. Re-run once on
25 October 2026 over the same period, on the data then held, with
acceptances that cross midnight joined; the method is unchanged.

**Earlier look.** An exploration read measure (1) on 11 October 2026.
Measure (2), the result, was added on 12 October 2026 and set to count
acceptances, as the BSC does, on 15 October 2026, both before measure (2)
was first read.

**Method:** per fuel group and month, two measures with n:

1. **Dispatch granularity.** The share of acceptances whose own span is
   under 10 minutes, and the median span.
2. **Continuous acceptance duration** (the result). For each
   acceptance, its continuous acceptance duration as BSC Annex T-1 Part 2
   paragraph 12 defines it for the CADL test: the unit's acceptances
   issued from the start of the settlement period three before the one in
   which it was issued to the end of the period three after are related to
   it; related acceptances that overlap or touch are chained; the duration
   runs from the earliest start to the latest end. Counted per acceptance,
   as the BSC counts it: the share of acceptances under 10 minutes, and
   the median duration.

Measure (2) is first checked against Elexon's `cadlFlag` over a 7-day
sample, excluding acceptances removed by the de minimis threshold, and the
agreement rate is reported.

### Q28. Are half-hours with a larger error in the API's stored carbon forecast associated with more unexplained shortfall, from August 2025 to August 2026?

The window is the year before GridSkew's forecast archive starts, so the
forecast is the API's stored forecast.

**Data:** the carbon outturn with the API's stored forecast, and the
cohort's unexplained shortfall.
**Analysis:** [q8_q28_history.sql](../dbt/analyses/q8_q28_history.sql),
shared with Q8.

**Status:** read on 26 October 2026, 11:21 UTC, with Q8.

**Method:**

- **Population.** The same as Q8.
- **Measure.** Per half-hour, the cohort sum of the positive part of
  `unexplained_shortfall_mwh` (under-delivery, as Q5) over determinable
  unit-periods.
- **Answer.** Quintiles of `abs(error_api_final)`. Ties are ordered by a
  fixed pseudo-random number (the first eight hexadecimal digits of the
  md5 of the seed, the bootstrap draw and pick, and the half-hour's epoch
  seconds), then by period start; the
  half-hours sharing the bottom quintile's largest error are reported. The
  answer is called "yes" (`supported` in the saved output) when the top
  quintile's mean is at least 25% above the bottom's, otherwise "no"
  (`not supported`).
- **Edge.** If the bottom mean is 0 or less, both means and their
  difference are reported and the answer is not applicable.
- **Interval.** A 90% day-block bootstrap interval for the ratio (1,000
  draws; fewer than 900 usable: unavailable) is descriptive. Resampling
  whole days keeps the correlation within a day; correlation between days
  is not modelled.
- **Limitations stated with the result.** The forecast is the stored
  forecast (see [Terms](#terms)), not a day-ahead forecast; the overall
  error of that forecast had already been seen in a September 2026
  exploration; settlement runs differ by date (R3 to SF, as held at the
  read).
- **Half-hour alignment (added 7 November 2026, after the read).** This
  result pairs each carbon value with the Elexon half-hour of the same API
  label, as published. Against NESO's historic generation mix, whose wind
  equals Elexon's at the same half-hour in 99.1% of 17,280 half-hours, the
  API's actual is within 5 g/kWh in 45.8% of half-hours one half-hour after
  its label, against 35.8% at its own label (correlation of half-hour
  changes 0.61, against 0.43). This is a measured alignment, not a
  documented convention. The result stands as read; no claim is made about
  how the alignment bears on it.

### Q29. How does the error of the API's stored carbon forecast vary by month and by time of day, over a year of history?

**Data:** the carbon outturn with the API's stored forecast.
**Analysis:**
[q29_final_forecast_error_by_month_and_block.sql](../dbt/analyses/q29_final_forecast_error_by_month_and_block.sql)

**Status:** read on 27 October 2026, 06:15 UTC.

**Method:**

- **Window.** Settlement dates 22 August 2025 to 16 August 2026, both
  included; half-hours from the settlement calendar.
- **Error.** The outturn minus the API's stored forecast, from the latest
  capture holding an observed value; positive means the half-hour was
  dirtier than forecast.
- **Population.** Every half-hour in the window with an error.
- **Splits, each on its own.** The month of the settlement date (August
  2025 and August 2026 are partial and labelled), and the four blocks of
  the half-hour's UTC start: 00:00-05:59, 06:00-11:59, 12:00-17:59,
  18:00-23:59.
- **Per cell and overall.** n, mean absolute error, mean error, median
  error and the share of errors equal to 0, with the half-hours in the
  window with and without an error.
- **Describe only.** No test, no threshold, no answer. The overall mean
  and median were seen in a September 2026 exploration (measured as
  forecast minus actual, the opposite sign) and are marked as such.
- **Read** after the analysis and its checks on worked rows are accepted.
- **Limitations stated with the result.** The forecast is the stored
  forecast (see [Terms](#terms)); the outturn is NESO's estimate; each
  month is seen once; it is not a judgement of NESO's method.
- **Half-hour alignment (added 7 November 2026, after the read).** This
  result sets the API's actual against its forecast at the same label, as
  published. Against NESO's historic generation mix, whose wind equals
  Elexon's at the same half-hour in 99.1% of 17,280 half-hours, the API's
  actual is within 5 g/kWh in 45.8% of half-hours one half-hour after its
  label, against 35.8% at its own label (correlation of half-hour changes
  0.61, against 0.43). This is a measured alignment, not a documented
  convention, and how the forecast lines up is not settled. The result
  stands as read; no claim is made about how the alignment bears on it.

### Q30. Is the size of the miss between wind outturn and NESO's latest wind forecast associated with the size of the error in the API's stored carbon forecast, from August 2025 to August 2026?

**Data:** WINDFOR wind forecast issues and FUELHH wind outturn, back-filled
for a year of history; the cohort wind units' instructed curtailment from
the shortfall table; the carbon outturn with the API's stored forecast.
**Analysis:**
[q30_wind_vs_carbon_error.sql](../dbt/analyses/q30_wind_vs_carbon_error.sql)

**Status:** read on 27 October 2026, on data captured up to 14:03:55 UTC,
after a counts-only coverage check.

**Method:**

- **Window.** Settlement dates 22 August 2025 to 16 August 2026;
  half-hours from the settlement calendar.
- **Forecast.** The latest WINDFOR issue with publish time strictly before
  the half-hour's start, and its hourly point containing the half-hour: no
  interpolation, no fall-back. As a working inference (not documented),
  made before any wind data was held, issues come at fixed UTC times,
  with summer publish times shown in BST but labelled Z and the forecast
  points' times true UTC. The pairing takes the latest source-stamped
  issue: under the inference it never uses an issue before it existed, and
  in summer it may use one up to an hour older than the newest available.
  Forecast ages are reported from the source stamp, by month; in summer
  they may be an hour short, a limitation stated with the result.
- **Outturn.** The first FUELHH wind publication.
- **Wind error.** Primary: outturn plus the cohort wind units' summed
  `instructed_deviation_mwh` × 2, minus forecast, labelled "outturn with
  cohort curtailment add-back" (WINDFOR's constraint basis is not
  documented). Sensitivity: delivered outturn minus forecast. A half-hour
  with a wind unit accepted but a null deviation is left out of both
  versions and counted.
- **Carbon error.** `error_api_final`: the outturn minus the API's stored
  forecast, as Q28 and Q29.
- **Population.** Half-hours with `error_api_final`, a forecast and an
  outturn; no matched-day threshold. Coverage by reason, by forecast-age
  band (at most 1 hour, over 1 to 2, over 2 to 4, over 4) and dates with
  fewer than 46 half-hours.
- **Measures, describe only.** Primary: Spearman `rho_abs`, the absolute
  wind error with the absolute carbon error. Secondary: `rho_signed`
  (negative when wind coming in short goes with carbon dirtier than
  forecast) and quintiles of the absolute wind error, with Q28's
  tie order on Q30's own seed. Both rhos on the sensitivity. 90% day-block bootstrap intervals
  (1,000 draws; with fewer than 900 usable draws, unavailable). No test,
  threshold or answer.
- **Earlier look.** One day of wind values (22 August 2025), during
  endpoint checks.
- **Read.** After the build checks and the counts-only coverage check
  pass.
- **Limitations stated with the result.** The carbon forecast is the
  stored forecast (see [Terms](#terms)), against a wind forecast up to
  hours old; metered wind only, with no embedded wind; an association, not
  a cause.
- **Half-hour alignment (added 7 November 2026, after the read).** This
  result pairs each carbon value with the Elexon half-hour of the same API
  label, as published. Against NESO's historic generation mix, whose wind
  equals Elexon's at the same half-hour in 99.1% of 17,280 half-hours, the
  API's actual is within 5 g/kWh in 45.8% of half-hours one half-hour after
  its label, against 35.8% at its own label (correlation of half-hour
  changes 0.61, against 0.43). This is a measured alignment, not a
  documented convention. The result stands as read; no claim is made about
  how the alignment bears on it.

### Q37. When NESO turns wind farms down, what does it turn up in the same half-hours?

**Data:** BOALF flags and instruction intervals; PN.
**Analysis:**
[q37_wind_turn_down_and_turn_up.sql](../dbt/analyses/q37_wind_turn_down_and_turn_up.sql)

**Status:** read on 27 November 2026, 07:54 UTC, covering half-hours up to
the one starting at 23:30 UTC on 31 October 2026.

**Earlier look.** Q24 read wind's flagged share and its acceptance volume,
both directions together, on 15 October 2026. No figure by direction, by
half-hour or by what else was instructed in the same half-hour was read
before this method was written.

**Method:**

- **Measure.** Q24's acceptance volume with its direction kept. For the
  minutes each acceptance is in force, the energy between the instructed
  level and the PN is turned down where the instructed level is below the
  PN and turned up where it is above. Flagged means the acceptance's SO
  flag is true; an acceptance with no flag value counts as not flagged.
  The query returns energy and counts of half-hours; shares are worked
  from them.
- **Units.** Every unit with an acceptance and a complete PN capture: cohort
  units by fuel group, and units with no published fuel type as one group
  of their own.
- **Half-hours.** Every half-hour in which at least one wind unit has a
  complete PN capture, from the first half-hour that holds an acceptance
  to the cutoff.
- **Part 1, wind turned down.** Per settlement month and over all months:
  the flagged energy turned down at wind units; all energy turned down at
  them; the flagged energy as a share of the energy the wind units planned
  (their PN energy); and the share of half-hours with any flagged wind
  turn-down, with 100 MW or more and with 1 GW or more. A half-hour's MW
  is its energy in MWh times two.
- **Part 2, what is turned up.** Each half-hour goes in a band by its
  flagged wind turn-down: none; under 100 MW; 100 MW to under 500 MW;
  500 MW to under 1 GW; 1 GW to under 2 GW; 2 GW or more. Per band and
  fuel group: the energy turned up and the energy turned down, each with
  its flagged part, and the number of half-hours in the band. The result
  is each group's share of the energy turned up in half-hours with any
  flagged wind turn-down, and its energy turned up per half-hour, each
  beside the same figure for half-hours with none.
- **Left out.** A stretch of an acceptance is left out when no single ramp
  segment of the acceptance spans it or its half-hour has no complete PN
  capture; left-out stretches are counted by fuel group and flag. A
  half-hour with a wind stretch left out goes in a band of its own, wind
  turn-down not measured, and in neither side of the comparison. Energy
  in a half-hour outside the population is counted beside the result.
- **Turn-up is counted with or without the flag.** Energy turned up to
  replace energy turned down behind a constraint need not carry the flag.
- **Wording.** "In the same half-hours", never "replaced by": the result
  is an association in time, not a matched pair of instructions. No
  location and no price is read.
- **Limits.** Only Balancing Mechanism acceptances are read: no trades
  and no wind outside the Balancing Mechanism. At a storage,
  interconnector or demand unit, turned up can mean importing less.
  Half-hours with wind turned down differ from the others in wind and
  demand, and nothing here controls for that.
- **Superseded acceptances (added 27 November 2026, after the read).**
  For the minutes where a later acceptance supersedes an earlier one,
  all of the energy between the later acceptance's level and the PN
  carries the later acceptance's flag. This can affect which band a
  half-hour falls in.

## Retired

Dropped questions stay listed, with the reason.

| ID | Question | Why retired |
|---|---|---|
| Q7 | Do wind shortfalls follow weather-forecast misses? | NESO expects wind units' PNs to use data near gate closure, so a day-ahead regional weather miss is a weak explanation; a limitation, not a settled exclusion. |
| Q11 | Are system prices revised after the first calculation? | Elexon's public system-prices endpoint serves only the first calculation, so revisions cannot be observed; this is not a finding that prices are never revised. |
| Q13 | Does interconnector flow explain forecast error? | No flow forecast is held; a question on outturn flows would be new, with its own source and method. |
| Q14 | Is the forecast worse on weekends, holidays or in high wind? | Its calendar part is answered inside Q10; Q30 reads the wind forecast's miss, not high wind. |
| Q17 | Capture timeliness per source and day | An operations measure, not research. Retired 12 October 2026. |
| Q18 | Storage delivery fidelity against thermal units | Q23 reports how closely each fuel group, storage and thermal plant included, follows its PN; Q25 covers instructions. A test of storage against thermal plant would be its own question. |
| Q19 | Instructed wind reductions per day | Q24 describes wind's system-action share and Q37 the energy turned down by month; a daily chart would be its own question. |
| Q20 | GridSkew-observed replay at a fixed cutoff | It does not bear on the main question, and the time GridSkew first held each record is an inferred bound, not a measured time. Dropped 9 October 2026. |
| Q21 | Registry churn | Low analytical value. Dropped before 9 October 2026. |

## How the questions were set up

Each method was written before the query that answers it and before its
result was read, except where listed under [Exceptions](#exceptions); the
table below gives the dates kept. Each result was read once, at a fixed cutoff
(`analysis_read_until`); Q25 was re-run once over the same period. A method
changed after its data is read makes a new question, and a change made
after a look at the data says so in the question. A question without a
written method is not reported as a result. The
[forecast read controls](#forecast-read-controls) were written on
25 October 2026, before Q1 or Q10 was read.

### Exceptions

Where a method was not wholly written before its data was seen:

- Q24: the measure was changed after an exploration had read the data.
- Q1 and Q10: Q1's thresholds were set after an exploration had seen 380 of
  its half-hours, and whether they were set knowing it is not recorded; the
  same exploration saw Q10's measure, error by lead time, on 365 of its
  population's half-hours. The mean drift and the correlation beside Q1's
  answer were added after related figures had been seen.
- Q29: part of it had been seen in an exploration before the method was
  written (the overall mean and median error).
- Q25: an exploration had read measure (1) before the method was written;
  measure (2), the result, was added before any read of it. It was re-run
  once over the same period, with acceptances that cross midnight joined; the
  method is unchanged.
- Q28: the overall error of its forecast had already been seen in a
  September 2026 exploration.
- Q28's reverse cut (on the results page): asked after Q28 was read.

### When each method was written

Dates are in 2026. A change listed here was made before the read; a note
added after a read is dated in its question.

| ID | Written | Changed | Data at that point |
|---|---|---|---|
| Q1 | 5 October | 6, 12, 13, 25 and 31 October | no drift value read |
| Q2 | 25 September | | no metered quantity read |
| Q3 | before its read, date not kept | | |
| Q5 | before its read; population and definitions 12 October | | no Q5 value read |
| Q6 | before its read; population 11 October | | no lag read |
| Q8 | before its read; completed 25 October | | no demand value held; a counts-only check of the demand data's coverage on 26 October |
| Q10 | before 12 October | 12, 25 and 31 October | not read |
| Q12 | restated 11 October | | no Q12 read |
| Q22 | 9 October | | no Q3 or Q22 query run |
| Q23 | 12 October | 12 October | not read |
| Q24 | 12 October | 12 October | level-energy shares read on 11 October |
| Q25 | 12 October | 12 and 15 October | measure (1) read on 11 October; measure (2) not read |
| Q28 | 25 October | 26 October | no figure of it read |
| Q29 | 27 October | | no figure of it read |
| Q30 | 27 October | 27 October | no wind data held when written; not read when changed |
| Q37 | 27 November | 27 November | Q24's acceptance volume for wind read on 15 October; no figure by direction or by half-hour read |
