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

## The mechanism being tested

Suppliers commit to output ahead of time, some fail to deliver, and gas peaking
plants are dispatched at short notice to fill the gap. Gas is dirtier than
most of what it replaces.

If that is what happens, then **carbon intensity forecast error should skew
positive** — actual intensity above forecast more often than below. That is a
falsifiable prediction and it is the core of the project.


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

## Honest limitations

Stated up front rather than buried at the bottom:

- **The archived carbon "forecast" is a late revision.** True 48-hour-ahead
  error is only measurable from this project's own archive onwards.
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
- [ ] Forecast archive running
- [ ] Endpoint shapes confirmed against the live API
- [ ] Hour-zero asymmetry test
- [ ] dbt project initialised
