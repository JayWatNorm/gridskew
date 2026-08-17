# gridskew

Measuring GB electricity grid shortfalls and their effect on carbon intensity
forecast error.

> **Status: Phase 0 — infrastructure and data collection only.**
> No analysis results yet, and no conclusions are claimed.

---

## The question

Three steps, in order:

1. **Find the shortfalls.** Where did generating units commit to produce power
   and then not produce it? Physical Notifications (`PN` — what each BM unit
   told the system operator it would do) against actual metered output
   (`B1610`), per unit, per half-hour settlement period.
2. **Measure the carbon impact.** Join those shortfalls to the error in the
   national carbon intensity forecast for the same periods.
3. **Explain them.** Split by cause: planned versus unplanned outages
   (`REMIT`), demand forecast error, and market stress signals.

Unit-level grain throughout. Aggregating to fuel type first washes out the
signal.

Running alongside those three, and independent of them, is a fourth question
that only this repository's own data can answer: **how does a forecast for a
given half-hour change as that half-hour approaches?** It needs no Elexon data
and no settlement lag, so it is the first question here that will have an
answer.

## The mechanism being tested

Suppliers commit to output ahead of time, some fail to deliver, and gas peaking
plants are dispatched at short notice to fill the gap. Gas is dirtier than
most of what it replaces.

If that is what happens, two things follow. Both are falsifiable, and together
they are the core of the project.

1. **Forecast error should skew positive** — actual carbon intensity above
   forecast more often than below.
2. **Forecasts should revise upward as the settlement period approaches.**
   Short-notice gas dispatch is information the model does not have 48 hours
   ahead and does have 30 minutes ahead. So the sequence of forecasts issued
   for a single period should drift upward as that period nears, and the size
   of that drift measures how much unforecast dirty generation arrived late.

The second prediction is the stronger test, for two reasons. It is measurable
from this repository's archive alone — no per-unit Elexon data, no five-day
settlement lag — so it produces results as soon as the archive has a few weeks
of depth. And it is not attenuated: prediction 1 has to be measured against a
published forecast that was itself revised late, which corrects part of the
effect out of the number before it can be seen.


## Why this repository archives forecasts

The Carbon Intensity API publishes a 48-hour-ahead forecast and **re-runs its
model every 30 minutes, overwriting the stored forecast in place.** Asking the
API today what it predicted for yesterday afternoon does not return the
48-hour-ahead prediction — it returns the final revision, made minutes before
the event, when the model already had most of the answer.

There is therefore no public record of how a forecast changed as it
approached, and true horizon-resolved forecast error cannot be measured from
the API alone.

So this project polls the forecast every 30 minutes and stores every revision
alongside the timestamp of the request. **That archive only extends forwards
from the day it starts. The past is not recoverable.** It is the first thing
built here for exactly that reason.

What the archive holds that nothing else does is the **revision path**: for one
settlement period, every forecast ever issued for it, from roughly 48 hours out
to minutes before. That sequence is what prediction 2 above is tested against,
and it is deleted by the source as it is produced.

## Honest limitations

Stated up front rather than buried at the bottom:

- **Published forecast error is attenuated.** The API's stored forecast is a
  late revision, made when the model had already observed much of what it was
  predicting. True 48-hour-ahead error is only measurable from this project's
  own archive, forwards from the day it started.
- **A forecast revision has several causes, not one.** The model reruns on
  fresh weather and interconnector schedules as well as on late dispatch. An
  upward revision shows that new information moved the number; attributing that
  movement to unplanned shortfalls specifically is what the per-unit Elexon
  join exists to do.
- **Analysis trails real time by roughly a week.** Per-unit actual generation
  is first published about five working days after the event, then restated by
  later settlement runs.
- **Attribution is correlational.** This project claims accounting, not causal
  proof.
- **Physical Notifications are not equally meaningful for every unit type.**
  Batteries and virtual lead parties may baseline differently, so the shortfall
  analysis is scoped to physical generators first (CCGT, nuclear, wind).
- **Weather data, in a later phase, is a proxy.** Open-Meteo is not the feed
  the national forecast model actually consumes.

## Data sources

| Source | Used for |
|---|---|
| **Elexon Insights** | Physical notifications, per-unit generation, outage notices, balancing actions, demand forecast and outturn, imbalance prices |
| **NESO Carbon Intensity API** | National carbon intensity forecast and outturn, half-hourly |

Both are public and free. Phase 1 uses these two only — no cross-organisation
joins, deliberately.

## Stack

Python ingestion → PostgreSQL → dbt → Airflow, deployed to a self-hosted Linux
server. Separate development and production databases; scheduled runs write to
production only.

## Repository layout

```
ingestion/      Python ingestion package (bind-mounted by Airflow)
dags/           Airflow DAG definitions
sql/            Raw-layer DDL, applied manually per database
dbt/            dbt project (from Phase 1)
dbt_profiles/   dbt connection profile, credentials via env_var()
tests/          pytest suite for the ingestion code
docs/           Decision log
```

## Working practice

- Every change arrives as a pull request, including documentation.
- The raw layer is append-only. Nothing in it is ever updated or deleted.

## Status

**Phase 0 — gates**

- [x] Development and production databases provisioned
- [x] Forecast poller written, with tests
- [ ] Forecast archive running on a schedule
- [ ] Endpoint shapes confirmed against the live API
- [ ] Hour-zero asymmetry test
- [ ] dbt project initialised
