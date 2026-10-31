# Research questions

GridSkew answers these questions with public GB electricity data. Each
question names the data that answers it, the analysis that reads it and a
reading rule recorded before the data is read: what counts as an answer is
decided before any figure is seen. A question without a rule is not reported
as a finding. This page holds the rules and their status, not the results.

## How questions are read

- **The rule comes first.** Each reading rule is recorded, with its date,
  before the query that answers it. Changing a rule after its data is read
  makes a new question. A change informed by an exploratory figure already
  seen says so.
- **Each result is read once,** at a fixed cutoff (`analysis_read_until`).
- **Every number has its context:** n, the date range and coverage beside it.
- **Associations, not causes.** A result says "associated with", never
  "caused by".
- **A "not supported" result is a result** and is published.
- **REMIT notices.** The current version of a notice at a given time is its
  publication with the latest `publish_time` not after that time; ties go to
  the later `created_time`, then the higher `revision_number`. Its first
  publication is the one with the earliest `publish_time`; ties go to the
  earlier `created_time`, then the lower `revision_number`. Revision numbers
  are the participant's own counter: they repeat and can fall.
  [Design decisions](design-decisions.md#modelling)
- **Instructions.** A later acceptance (higher acceptance number) replaces
  an earlier one only for the minutes they share, and the PN applies where
  no acceptance does. Each ramp point is read from its latest capture.
  Instructed energy is never added up across the acceptances of one unit.
  [Design decisions](design-decisions.md#modelling)

**The thesis.** We test whether half-hours in which power stations miss
their own plans are also half-hours in which NESO's carbon forecast misses
by more. NESO builds its forecast from its own demand, wind and solar
forecasts, not from those plans, so any link is indirect. Q1 describes how
the forecast is revised, Q10 and Q29 how far it misses, and Q27 how often it
freezes. Q3, Q5, Q12, Q23 and Q26 describe delivery against plan; Q4 and
Q28 test its link to the forecast's miss; Q8, Q9 and Q30 test competing or
contextual explanations.

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
  <!-- TODO-AMEND(D12) -->
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
  `unexplained_after_absvd_mwh` is the unexplained shortfall with ABSVD
  added to the expected energy, as settlement adds it.
- **Slot poll:** the earliest forecast poll in a half-hour slot of its
  retrieval time.
- **Carbon forecast error:** actual minus forecast, in gCO2/kWh, so a
  positive error is a half-hour dirtier than forecast. The actual is NESO's
  own estimate.
- **`error_final`:** the error of the final forecast in GridSkew's own
  archive of every forecast (Q1's final forecast), at any horizon.
- **`error_api_final`:** the error of the stored forecast, the one value
  NESO's API keeps for each past half-hour; where GridSkew's archive could
  check it (17 August to 10 September 2026), it matched the forecast as it
  stood at about the end of the half-hour. The archive starts on 17 August
  2026, so the year before it has only this value.

### Forecast read controls

We read a forecast result only after the forecast feed has run past the
cutoff and the outturn has had eight days to settle. We save the query,
output and read time; limiting a rerun to the data held then is done by
hand. Recorded on 25 October 2026 for Q1, Q4 and Q10, before any of them was
read:

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
[q1_forecast_drift_splits.sql](../dbt/analyses/q1_forecast_drift_splits.sql);
the build is described in
[Carbon forecast trajectory](carbon-forecast-trajectory.md).

**Status:** registered 5 October 2026, before any drift value was read.
Amended before any drift value was read:

- 6 October 2026: the points the first rule left open are settled (how a
  no-change run is counted, horizons in exact seconds, which poll is the
  24-hour and 4-hour forecast, the sensitivity check's thresholds).
- 12 October 2026: the secondary splits are fixed as time of day and
  first-forecast band; the season split is dropped, as the data spans
  mid-August to November; no daily statistic is reported.
- 13 October 2026: the statistics each split cell reports are fixed.
- 25 October 2026: the forecast read controls are added.

Waiting for 2,880 complete half-hours.
<!-- TODO-AMEND(D6) -->

**Rule:**

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
- **Verdict.** Upward drift is supported when the median drift is at least
  +2 and the share of positive drifts, zeros excluded, is at least 55%;
  contradicted when the median is at most −2 and the positive share at most
  45%; otherwise there is no directional drift. The share test uses the
  exact share; the reported share is rounded to six decimals.
- **Sensitivity check.** The same verdict rule, recomputed with one poll
  per slot, no poll excluded, the same thresholds and no population minimum
  of its own. If the two verdicts differ, the result is "not robust to
  frozen polls" and no direction is reported.
- **Beside the verdict.** The date range, the complete half-hours per
  weekday and per half-hour of the day (UTC), and the half-hours removed by
  each exclusion, counted in order: partial polls first, then no-change
  polls.
- **Secondary splits, described and not tested.** Each cell reports n, the
  median drift and the positive share (positive drifts divided by non-zero
  drifts). (1) Time of day, in four blocks by each half-hour's UTC start:
  00:00-05:59, 06:00-11:59, 12:00-17:59, 18:00-23:59. (2) The source's
  `intensity_index` band (`very low`, `low`, `moderate`, `high`,
  `very high`) on the poll that gave the first forecast; a poll with no
  band is counted as unclassified, never replaced by a later band.
- **One look.** The verdict is read once, with `analysis_read_until` on
  the first day the population reaches 2,880, under the
  [forecast read controls](#forecast-read-controls). Before then the query
  returns the count and no result.
- The verdict shows the usual direction of revision, not whether the
  forecast is efficient.

### Q2. How much does metered output change between settlement runs?

**Data:** B1610 metered output, interim (II), initial (SF) and first
reconciliation (R1) runs.
**Analysis:** not in this repository.

**Status:** registered 25 September 2026, before any metered quantity was
read. Read once on 11 October 2026.

**Rule:**

- **Population.** Settlement dates 10 to 16 August 2026, every BM unit;
  two run pairs, II to SF and SF to R1, over matched unit-periods.
- **Measures, per run pair.** Moved share = Σ|later − earlier| ÷
  Σ|earlier|; changed share = unit-periods whose quantity changed ÷ matched
  unit-periods.
- **Labels.** Moved share: small below 0.5%, material at 2% or more.
  Changed share: small below 5%, material at 20% or more. A value between
  the limits is intermediate. A pair is material when either measure is
  material, small when both are small, otherwise intermediate. Labels are
  tested on the exact shares.
- **What it decides, on SF to R1 only.** Both small: R2 and R3 are not
  captured and RF alone is enough. Either material: a bounded R2 capture is
  proposed. Otherwise: reported as intermediate, no action. II to SF is
  reported beside it and does not enter the decision.

### Q2-RF. How much does metered output change from R3 to RF?

**Data:** B1610 R3 and final reconciliation (RF) runs; RF is captured from
15 November 2026, for settlement dates from 22 August 2025.
**Analysis:**
[q2rf_restatement_r3_to_rf.sql](../dbt/analyses/q2rf_restatement_r3_to_rf.sql)

**Status:** registered 12 October 2026, before any RF value was compared.
Waiting for 30 settlement dates holding both runs (about mid-December 2026).
<!-- TODO-AMEND(D11) -->

**Rule:**

- **Population.** The first 30 settlement dates whose RF is stored that
  also hold R3.
- **Measures, as Q2's moved share; no labels.** Gross unit-level change Σ|RF − R3| ÷ Σ|R3| over
  unit-periods holding both, by fuel group and overall, with the net change
  beside it; n, dates and coverage.
- **Edge.** If Σ|R3| is zero for a group or overall, the numerator,
  denominator and n are reported and that ratio is marked not defined,
  never zero.
- **One look.** Read once, when 30 dates are stored. Any count-only
  comparison of storage methods uses dates outside these first 30 dates
  with RF and R3.

### Q3. Is metered output usually above or below the notified position (PN), by fuel group?

**Data:** PN and B1610.
**Analysis:**
[q3_metered_vs_notified.sql](../dbt/analyses/q3_metered_vs_notified.sql),
with coverage in [q3_coverage.sql](../dbt/analyses/q3_coverage.sql).

**Status:** registered before its read. Read once on 11 October 2026.

**Rule:** describe only: the median and interquartile range of
`deviation_from_pn_mwh` per fuel group, with coverage beside it. The
analysis reads determinable cohort periods with a PN. The median is signed,
so it shows direction, not distance. This is not NESO's PN-accuracy measure
(see Q23).

### Q4. Are periods with a larger carbon forecast error associated with more unexplained shortfall?

**Data:** the forecast error of Q1's half-hours and the cohort's unexplained
shortfall.
**Analysis:**
[q4_forecast_error_vs_shortfall.sql](../dbt/analyses/q4_forecast_error_vs_shortfall.sql)

**Status:** registered before 12 October 2026: the top quintile's mean
unexplained shortfall at least 25% above the bottom's, over at least 90
days. Amended before any read:

- 12 October 2026: matched days defined, the measure changed from signed
  shortfall to under-delivery only, edge cases and the bootstrap interval
  added.
- 25 October 2026: the quintiles use only half-hours with a fresh final
  forecast; the forecast read controls are added.
- 26 October 2026: ties in the error are ordered by a fixed pseudo-random
  number instead of by date. No count of ties was taken before this change.

Waiting for 90 matched days.
<!-- TODO-AMEND(D5) -->

**Rule:**

- **Population.** Half-hours on matched days. A matched day is a settlement
  date with both `error_final` (outturn minus the archive's final forecast,
  at any horizon; not the API's stored forecast) and the cohort's
  unexplained shortfall for
  at least 46 half-hours; at least 90 matched days, up to a fixed
  `analysis_read_until` recorded before the read.
- **Fresh final forecast.** The quintiles and the bootstrap use only
  half-hours whose final forecast was made at most 30 minutes ahead (Q1's
  "final"). Matched days are still counted at any final horizon, so a stall
  does not drop a day. The bootstrap resamples only matched days holding at
  least one such half-hour. The half-hours and matched days left out are
  reported. The result describes half-hours with a fresh final forecast,
  not every half-hour.
- **Measure.** Per half-hour, the cohort sum of the positive part of
  `unexplained_shortfall_mwh` (under-delivery, as Q5).
- **Test.** Quintiles of `abs(error_final)`. Ties are ordered by a fixed
  pseudo-random number (md5 of the seed, the bootstrap draw and pick, and
  the half-hour's epoch seconds), then by period start; the half-hours
  sharing the bottom quintile's largest error are reported. "Supported" if
  the top quintile's mean is at least 25% above the bottom quintile's,
  otherwise "not supported".
- **Edge.** If the bottom quintile's observed mean is less than or equal to
  0, both means and their difference are reported and the ratio test and
  its interval are not applicable.
- **Interval.** A 90% day-block bootstrap interval (1,000 draws) for the
  ratio is reported beside the verdict and does not change it. A draw whose
  bottom mean is not positive forms no ratio; if fewer than 900 draws
  remain, the interval is reported as unavailable.
- **One look.** n, date range and coverage beside every number. Read once,
  with `analysis_read_until` on the first day the population reaches 90
  matched days, under the
  [forecast read controls](#forecast-read-controls).

### Q5. How much positive unexplained shortfall coincides with outages published in REMIT at the period start?

**Data:** REMIT publications and the cohort's unexplained shortfall.
**Analysis:**
[q5_shortfall_with_known_outage.sql](../dbt/analyses/q5_shortfall_with_known_outage.sql)

**Status:** registered before its read. Population and definitions recorded
12 October 2026, before any Q5 value was read. Read once on 12 October 2026,
21:19 UTC, with `analysis_read_until` = 2026-09-27 22:30 UTC.

**Rule:**

- **Measure.** The share of unexplained shortfall MWh in half-hours with a
  known outage, split planned and unplanned; no verdict, a measured share.
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
- **Read.** Once, with `analysis_read_until` = 2026-09-27 22:30 UTC (the
  last complete II settlement date), after a counts-only check that the
  REMIT views answer in time on a quiet server; the unedited output saved.
- "Known" means published in REMIT at the half-hour's start, not that the
  generator knew. An association, not a cause.

### Q6. How long after its written start is an outage notice first published?

**Data:** REMIT `publish_time` against `event_start_time`.
**Analysis:**
[q6_publication_lag.sql](../dbt/analyses/q6_publication_lag.sql)

**Status:** registered before its read. Population recorded 11 October 2026,
before any lag was read. Read once on 12 October 2026.

**Rule:**

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
  show when the participant knew. ACER's one-hour guidance and Regulation
  543/2013 Article 15 are context, not a compliance test.
- "Dismissed" means dismissed as of the read, so the result cannot be
  regenerated exactly later.

### Q8. Is demand forecast error more strongly associated with carbon forecast error than shortfall is?

**Data:** national demand forecast (NDF) and Initial National Demand
Outturn (INDO) issues for a year of history, the carbon outturn with the
API's stored forecast, and the cohort's unexplained shortfall.
**Analysis:** [q8_q28_history.sql](../dbt/analyses/q8_q28_history.sql),
shared with Q28.

**Status:** registered before its read: compare the rank correlation of
`abs(error_final)` with `abs(demand_error_final_mw)` and with unexplained
shortfall over the same periods, report both with n,
and call one "stronger" only if the gap is at least 0.1. Completed
25 October 2026, before any demand value was held: the carbon error
(`error_api_final`), the shortfall's aggregation and sign, the two final-forecast cutoffs, the shared period set
and an interval beside the gap. A counts-only coverage check of the demand
data was recorded on 26 October 2026, before the read. Read once on
26 October 2026, 11:21 UTC.

**Rule:**

- **Window.** Settlement dates 22 August 2025 to 16 August 2026, read with
  Q28 on one shared population.
- **Carbon error.** `error_api_final`: the error of the stored forecast
  (see [Terms](#terms)); the only carbon forecast held for that year.
- **Demand error.** The first INDO outturn issued for the half-hour minus
  the last NDF issued before the half-hour starts, national boundary.
- **Shortfall.** Per half-hour, the cohort sum of the positive part of
  `unexplained_shortfall_mwh`, as Q4 and Q5.
- **Population.** Matched days with all three present for at least 46
  half-hours; every half-hour on them with all three.
- **Measure.** Spearman `rho_demand` (absolute carbon error with absolute
  demand error) and `rho_shortfall` (absolute carbon error with the
  shortfall), each with n; gap = `rho_demand` − `rho_shortfall`.
- **Verdict.** Demand is stronger at a gap of 0.1 or more, shortfall at
  −0.1 or less; otherwise there is no clear difference.
- **Interval.** A 90% day-block bootstrap percentile interval for the gap
  (1,000 draws, both correlations on the same resampled half-hours) is
  descriptive and does not move the verdict. A draw with a constant measure
  forms no gap; with fewer than 900 valid draws the interval is
  unavailable.
- **One look.** Read once, after the demand back-fill and its counts-only
  coverage check.

### Q9. Is the system under stress (high NIV, high price) when unexplained shortfall is high?

**Data:** system prices and net imbalance volume (NIV), not yet collected.
**Analysis:** not yet written.

**Status:** registered; waiting for the system-price data, a later
extension.
<!-- TODO-AMEND(D4) -->

**Rule:** describe the mean `niv_percentile` in the top
unexplained-shortfall decile against all periods, over a fixed window. Net
imbalance volume includes the cohort's own shortfall, so part of any link
is built in: an association only.

### Q10. How accurate is the forecast at each lead time?

**Data:** the forecast archive and outturn.
**Analysis:**
[q10_forecast_accuracy_by_lead_time.sql](../dbt/analyses/q10_forecast_accuracy_by_lead_time.sql)

**Status:** registered before 12 October 2026: mean absolute error and bias
at 24 hours, 4 hours and final. Amended 12 October 2026, before any read:
one common sample, the name "accuracy by lead time", and the calendar
split. Forecast read controls added 25 October 2026, before any read.
Waiting for Q1's one look.
<!-- TODO-AMEND(D2) -->

**Rule:**

- **Measure.** Accuracy by lead time, not "skill" (which means against a
  reference forecast): the mean absolute error and bias of the forecast at
  24 hours, 4 hours and final, against outturn.
- **Horizons.** The 24-hour and 4-hour forecasts are the counted poll
  nearest that horizon, and null when none is within half an hour of it;
  the final forecast is Q1's.
- **Sample.** One common sample: Q1's one-look half-hours that have all
  three horizons, with each horizon's n and the half-hours dropped
  reported.
- **Split.** By settlement-date type: weekday, weekend and bank holiday
  (England and Wales), described with n; bank holidays are not tested (too
  few).
- **One look.** Read once, after Q1's one look, at the same
  `analysis_read_until`, under the
  [forecast read controls](#forecast-read-controls).

### Q12. How much of the deviation from PN was instructed (BOALF) rather than unexplained?

**Data:** BOALF, PN and B1610.
**Analysis:**
[q12_instructed_vs_residual.sql](../dbt/analyses/q12_instructed_vs_residual.sql)

**Status:** restated 11 October 2026, before any Q12 read, to cover every
determinable cohort period with a PN rather than only fully instructed
periods. Read once on 11 October 2026.

**Rule:**

- **Measure.** Describe, per fuel group, over every determinable cohort
  period with a PN, the instructed part as a share of the components'
  sizes, not of each period's own deviation: Σ|`instructed_deviation_mwh`|
  ÷ (Σ|`instructed_deviation_mwh`| + Σ|`unexplained_shortfall_mwh`|), with
  n, instructed periods and the three absolute sums beside it.
- ABSVD is not counted (see Q26).
- **At the read.** Overlapping acceptances were ordered by acceptance time,
  then acceptance number. The model orders them by acceptance number, the
  balancing mechanism's own order, so a rebuild gives different values; the
  two orders differ for 32 of 3,620,900 acceptances.

### Q22. Did later meter data change one week's fuel-group picture?

**Data:** B1610 runs and the Q3 facts for Q2's settlement dates.
**Analysis:** not in this repository.

**Status:** registered 9 October 2026, before any Q3 or Q22 query. Read
once on 11 October 2026.
<!-- TODO-AMEND(D9) -->

**Rule:**

- **Measure.** The Q3 statistics (median and interquartile range of
  `deviation_from_pn_mwh` per fuel group) on the first run and the latest
  run of settlement dates 10 to 16 August 2026, side by side.
- **Verdict.** The headline changes when the order of the fuel groups by
  median deviation changes, or when any group's median changes sign. No
  numeric threshold.
- **At the read.** "Latest" was R1, so the read compares II with R1. There
  is no re-read; an II-to-RF comparison would be a new question,
  registered before those values exist.

### Q23. How close to their PNs do units run when not instructed?

A proxy for NESO's PN-accuracy measure.

**Data:** PN, B1610, BOALF and registry capacity.
**Analysis:**
[q23_pn_accuracy_proxy.sql](../dbt/analyses/q23_pn_accuracy_proxy.sql)

**Status:** registered 12 October 2026. Amended 12 October 2026, before
any read: interconnector units are excluded. Read once on 15 October 2026,
20:05 UTC, with `analysis_read_until` = 2026-09-30 22:30 UTC.

**Rule:**

- **Population.** Per cohort unit and calendar month, determinable
  half-hours with no acceptance in force for any minute of the half-hour
  (`instruction_status = 'uninstructed'`); NESO likewise excludes any
  half-hour with a bid-offer acceptance. Interconnector units are excluded,
  as NESO's monitoring excludes them. Units with no registry capacity are
  left out and counted.
- **Measures.** Net % = Σ(PN − metered) ÷ Σ(capacity × 0.5 h); absolute
  % = Σ|PN − metered| ÷ Σ(capacity × 0.5 h).
- **Reported.** The median and 90th percentile per fuel group and month.
- **Declared split.** Days with SO-flagged wind bids against days without.
- **A proxy.** ABSVD and dynamic MEL/MIL are not used, so NESO's thresholds
  are not applied and no unit is called inaccurate. This rule is the
  registered result.
- **Sensitivity.** With ABSVD captured, an ABSVD-inclusive version (ABSVD
  added to the PN, as NESO's September 2026 guidance) is reported beside
  it, labelled a sensitivity analysis, its method fixed before it is run.
  It is informed by an exploration that had already shown ABSVD moving
  batteries' unexplained shortfall on some days, so it is not called
  pre-registered; the base result above is.

### Q24. What share of each fuel group's instructed energy is flagged as a system action rather than energy balancing?

**Data:** BOALF flags and instruction intervals.
**Analysis:**
[q24_system_action_share.sql](../dbt/analyses/q24_system_action_share.sql)

**Status:** registered 12 October 2026, after an exploration had read
level-energy shares on 11 October. Amended 12 October 2026 to measure
acceptance volume instead; because the data had already been explored, the
amended result is not called pre-registered. Read once on 15 October 2026,
20:10 UTC, with `analysis_read_until` = 2026-09-30 22:30 UTC.

**Rule:**

- **Measure.** Per fuel group and month, the acceptance volume flagged as
  a system action, as a share of all acceptance volume. Acceptance volume
  is the absolute difference between the instructed level and the PN over
  the minutes each acceptance is in force, as settlement measures it.
- **Beside it.** The level-energy share (the same share on the energy at
  the instructed level, not its difference from PN; the exploration's
  measure and the rule first registered) is reported for continuity.
- **Wording.** "Flagged as system actions", not "constraint volume": the
  flag is NESO's real-time classification, and later corrections are not
  re-read.

### Q25. How fragmented are instructions to units typed other (mostly batteries) and pumped storage compared with thermal plant?

**Data:** BOALF.
**Analysis:**
[q25_instruction_fragmentation.sql](../dbt/analyses/q25_instruction_fragmentation.sql)

**Status:** registered 12 October 2026 with measure (1), which an
exploration had already read on 11 October, so measure (1) is not called
pre-registered. Amended 12 October 2026, before any read of it, to add
measure (2), the registered result. Read once on 15 October 2026, 22:01
UTC, with `analysis_read_until` = 2026-09-30 22:30 UTC.
<!-- TODO-AMEND(D8) -->

**Rule:** per fuel group and month, two measures with n:

1. **Dispatch granularity.** The share of acceptances whose own span is
   under 10 minutes, and the median span.
2. **Continuous acceptance duration.** The duration of each chain of
   overlapping acceptances for a unit, as BSC Annex T-1 Part 2 paragraph 12
   defines it for the CADL test, and the share of chains under 10 minutes.

Before the read, measure (2) is checked against Elexon's `cadlFlag` over a
7-day sample, excluding acceptances removed by the de minimis threshold, and
the agreement rate is reported.

### Q26. How much of the deviation from PN do balancing actions explain once services settled outside the balancing mechanism (ABSVD) are counted?

**Data:** ABSVD (QAS), PN, B1610 and BOALF.
**Analysis:**
[q26_instructed_and_absvd_share.sql](../dbt/analyses/q26_instructed_and_absvd_share.sql)

**Status:** registered 12 October 2026, before any ABSVD capture. Not yet
read.
<!-- TODO-AMEND(D3) -->

**Rule:**

- **Population.** Per fuel group, determinable cohort periods with a PN
  whose settlement date has been captured (`absvd_mwh` not null).
- **Measure.** (Σ|`instructed_deviation_mwh`| + Σ|`absvd_mwh`|) ÷
  (Σ|`instructed_deviation_mwh`| + Σ|`absvd_mwh`| +
  Σ|`unexplained_after_absvd_mwh`|): a share of the components' sizes, not
  of each period's own deviation.
- **Beside it.** n, the three sums, the date range, the share of periods
  with any ABSVD and the periods left out for an uncaptured date; Q12's
  figure over the same periods, not replaced.
- **Edge.** N/A with its n where the denominator is zero.
- ABSVD is the latest value captured by the 450-day capture, not called
  final.
- **Prerequisite.** Read only after the counts-only resubmission check
  ([qas_resubmissions.sql](../dbt/analyses/qas_resubmissions.sql)). If a
  later capture drops a unit-period an earlier one held, how a dropped
  unit-period reads is decided before Q26 is read.

### Q27. How often, and for how long, does the published carbon-intensity forecast stop changing?

**Data:** the forecast archive's poll flags; no forecast value, outturn or
error.
**Analysis:**
[q27_forecast_stalls.sql](../dbt/analyses/q27_forecast_stalls.sql)

**Status:** registered 25 October 2026, before any poll in its window
existed. Amended 25 October 2026, before the window opened: the read waits
for three slot polls after the cutoff. Waiting for 90 days of polls (about
30 January 2027).
<!-- TODO-AMEND(D1) -->

**Rule:**

- **Window.** Slot polls from 2026-11-01 00:00 UTC to a fixed
  `analysis_read_until`. Polls after the cutoff are never counted.
- **Stall.** A run of no-change slot polls under Q1's exclusion rule: four
  or more in a row, partial polls counting towards the four, a slot with no
  poll not ending a run.
- **Reported, describe only, no test.** The number of stalls; the median
  and longest duration, both as polls × 30 minutes and as elapsed time
  (first to last poll's slot plus 30 minutes); the share of slot polls
  inside a stall; partial polls counted on their own, with the overlap;
  stalls by the UTC block of their first poll (Q1's four blocks; a split
  informed by the exploratory stalls, labelled so); slots with no poll and
  stalls cut at the window's edges.
- A stall's time block is that of its first poll, inside the window or
  not.
- The six stalls of 1 to 24 October 2026 are reported apart, labelled
  exploratory.
- **One look.** Read once, when the window holds 90 days and at least three
  slot polls (one fewer than the run of four) are stored after
  `analysis_read_until`, so a stall running past the cutoff is settled
  whatever slots are missing.

### Q28. Are half-hours with a larger error in the API's stored carbon forecast associated with more unexplained shortfall, from August 2025 to August 2026?

Q4's test on the year before Q4's window, with the stored forecast in place
of the archive's final forecast.

**Data:** the carbon outturn with the API's stored forecast, and the
cohort's unexplained shortfall.
**Analysis:** [q8_q28_history.sql](../dbt/analyses/q8_q28_history.sql),
shared with Q8.

**Status:** registered 25 October 2026, before any figure of it was read.
Amended 26 October 2026 with Q4, before any read: ties in the error are
ordered by the same fixed pseudo-random number. Read once with Q8 on
26 October 2026, 11:21 UTC.

**Rule:**

- **Population.** The same as Q8.
- **Measure.** Per half-hour, the cohort sum of the positive part of
  `unexplained_shortfall_mwh`.
- **Test.** Quintiles of `abs(error_api_final)`; ties ordered by the same
  fixed pseudo-random number as Q4, then period start; the half-hours
  sharing the bottom quintile's largest error are reported. Supported if
  the top quintile's mean is at least 25% above the bottom's, otherwise not
  supported.
- **Edge.** If the bottom mean is 0 or less, both means and their
  difference are reported and the test is not applicable.
- **Interval.** A 90% day-block bootstrap interval for the ratio (1,000
  draws; fewer than 900 usable: unavailable) is descriptive.
- **Limitations stated with the result.** The forecast is the stored
  forecast (see [Terms](#terms)), not a day-ahead forecast; the overall
  error of that forecast had already been seen in a September 2026
  exploration; settlement runs differ by date (R3 to SF, as held at the
  read).

### Q29. How does the error of the API's stored carbon forecast vary by month and by time of day, over a year of history?

**Data:** the carbon outturn with the API's stored forecast.
**Analysis:**
[q29_final_forecast_error_by_month_and_block.sql](../dbt/analyses/q29_final_forecast_error_by_month_and_block.sql)

**Status:** registered 27 October 2026, before any figure of it was read.
Read once on 27 October 2026, 06:15 UTC.

**Rule:**

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
- **Describe only.** No test, no threshold, no verdict. The overall mean
  and median were seen in a September 2026 exploration (measured as
  forecast minus actual, the opposite sign) and are labelled as such.
- **Read once,** after the analysis and its checks on worked rows are
  accepted.
- **Limitations stated with the result.** The forecast is the stored
  forecast (see [Terms](#terms)); the outturn is NESO's estimate; each
  month is seen once; it is not a judgement of NESO's method.

### Q30. Is the size of the miss between wind outturn and NESO's latest wind forecast associated with the size of the error in the API's stored carbon forecast, from August 2025 to August 2026?

**Data:** WINDFOR wind forecast issues and FUELHH wind outturn, back-filled
for a year of history; the cohort wind units' instructed curtailment from
the shortfall table; the carbon outturn with the API's stored forecast.
**Analysis:**
[q30_wind_vs_carbon_error.sql](../dbt/analyses/q30_wind_vs_carbon_error.sql)

**Status:** registered 27 October 2026, before any wind data was held.
Amended the same day, before any read: the carbon error is the one Q28 and
Q29 read. A counts-only coverage check was recorded before the read. Read
once on 27 October 2026, on data captured up to 14:03:55 UTC.

**Rule:**

- **Window.** Settlement dates 22 August 2025 to 16 August 2026;
  half-hours from the settlement calendar.
- **Forecast.** The latest WINDFOR issue with publish time strictly before
  the half-hour's start, and its hourly point containing the half-hour: no
  interpolation, no fall-back. As a working inference (not documented),
  recorded before any wind data was held, issues come at fixed UTC times,
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
  (expected negative) and quintiles of the absolute wind error, with Q4's
  tie key. Both rhos on the sensitivity. 90% day-block bootstrap intervals
  (1,000 draws; with fewer than 900 usable draws, unavailable). No test,
  threshold or verdict; the expectation stated before the read, an
  absolute rho under about 0.2, is not a test.
- **Already seen.** One day of wind values (22 August 2025), during
  endpoint checks.
- **One look.** Read once, after the build review and the counts-only
  coverage check.
- **Limitations stated with the result.** The carbon forecast is the
  stored forecast (see [Terms](#terms)), against a wind forecast up to
  hours old; metered wind only, with no embedded wind; an association, not
  a cause.

## Candidates

Each candidate needs a reading rule recorded before it is designed in.

<!-- TODO-AMEND(D13) -->

| ID | Question | Status |
|---|---|---|
| Q15 | Is the published REMIT capacity consistent with metered output during outages? | Kept as a candidate: a data-quality question about REMIT itself. |
| Q16 | Carbon-aware jobs: how much lower would the forecast-chosen window's intensity be than a fixed start, for fixed workloads? | Held until Q1 and Q10 are read: an applied study, not a test of the thesis; simulated intensity only, no absolute CO2. |
| none | Is a frozen forecast worse? | Raised by Q27; no error in a stall is read before it has its own rule. |

## Retired

Dropped questions stay listed, with the reason.

| ID | Question | Why retired |
|---|---|---|
| Q7 | Do wind shortfalls follow weather-forecast misses? | NESO expects wind units' PNs to use data near gate closure, so a day-ahead regional weather miss is a weak explanation; a limitation, not a settled exclusion. |
| Q11 | Are system prices revised after the first calculation? | Elexon's public system-prices endpoint serves only the first calculation, so revisions cannot be observed; this is not a finding that prices are never revised. |
| Q13 | Does interconnector flow explain forecast error? | No flow forecast is held; a question on outturn flows would be new, with its own source and rule. |
| Q14 | Is the forecast worse on weekends, holidays or in high wind? | Its calendar part is answered inside Q10; Q30 reads the wind forecast's miss, not high wind. |
| Q17 | Capture timeliness per source and day | An operations measure, not research, covered by the health read at the start of each working session. Retired 12 October 2026. |
| Q18 | Storage delivery fidelity against thermal units | Q23 reports how closely each fuel group, storage and thermal plant included, follows its PN; Q25 and Q26 cover instructions and services settled outside the balancing mechanism. A test of storage against thermal plant would be its own registered question. |
| Q19 | Instructed wind reductions per day | Q24 describes wind's system-action share; a daily chart would be its own registered question. |
| Q20 | GridSkew-observed replay at a fixed cutoff | It answers no thesis question, and the time GridSkew first held each record is an inferred bound, not a measured time. Dropped 9 October 2026. |
| Q21 | Registry churn | Low analytical value. Dropped before 9 October 2026. |
