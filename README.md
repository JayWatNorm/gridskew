# gridskew

Measuring GB electricity grid shortfalls and their effect on carbon intensity
forecast error.

> **Phase 0 — infrastructure and data collection only.** No analysis results
> yet, and no conclusions are claimed.

---

## The question

1. **Find the shortfalls.** Where did generating units commit to produce power
   and then not produce it? Physical Notifications (`PN`) against actual
   metered output (`B1610`), per unit, per half-hour settlement period.
2. **Measure the carbon impact.** Join those shortfalls to the error in the
   national carbon intensity forecast for the same periods.
3. **Explain them.** Planned versus unplanned outages (`REMIT`), demand
   forecast error, market stress signals.

Unit-level grain throughout — aggregating to fuel type first washes out the
signal.

A fourth question runs alongside and independent of those three: **how does a
forecast for a given half-hour change as that half-hour approaches?** It needs
no Elexon data and no settlement lag, so it is the first question here that
will have an answer.

## The mechanism being tested

Suppliers commit to output ahead of time, some fail to deliver, and gas peakers
are dispatched at short notice to fill the gap. Gas is dirtier than most of
what it replaces.

Two falsifiable predictions follow:

1. **Forecast error should skew positive** — actual intensity above forecast
   more often than below.
2. **Forecasts should revise upward as the period approaches.** Short-notice
   gas dispatch is information the model lacks 48 hours out and holds 30
   minutes out.

The second is the stronger test. It is measurable from this repository's
archive alone, and it is not attenuated — prediction 1 has to be measured
against a published forecast that was itself revised late.

## Why this repository archives forecasts

The Carbon Intensity API publishes a 48-hour-ahead forecast and **re-runs its
model every 30 minutes, overwriting the stored value in place.** Ask it today
what it predicted for yesterday afternoon and you get the final revision, made
minutes before the event, when the model already had most of the answer.

So there is no public record of how a forecast changed as it approached. This
project polls every 30 minutes and stores every revision with the timestamp of
the request. **The archive only extends forwards from the day it starts. The
past is not recoverable** — which is why it was the first thing built.

## Honest limitations

- **Published forecast error is attenuated.** The API's stored forecast is a
  late revision. True 48-hour-ahead error is only measurable from this
  project's own archive, forwards from the day it started.
- **A revision has several causes.** Model reruns, fresh weather and
  interconnector schedules move the number too. Attributing movement to
  unplanned shortfalls specifically is what the per-unit Elexon join is for.
- **Analysis trails real time by about a week.** Per-unit generation is
  published roughly five working days after the event, then restated.
- **Attribution is correlational.** This claims accounting, not causal proof.
- **Physical Notifications mean different things by unit type.** Batteries and
  virtual lead parties baseline differently, so the shortfall analysis is
  scoped to physical generators first (CCGT, nuclear, wind).
- **Weather data, in a later phase, is a proxy.** Open-Meteo is not the feed
  the national forecast model consumes.

## Data sources

| Source | Used for |
|---|---|
| **Elexon Insights** | Physical notifications, per-unit generation, outage notices, balancing actions, demand, imbalance prices |
| **NESO Carbon Intensity API** | National carbon intensity forecast and outturn, half-hourly |

Both public and free. Phase 1 uses these two only — no cross-organisation
joins, deliberately.

## Stack

Python ingestion → PostgreSQL → dbt → Airflow, on a self-hosted Linux server.
Separate development and production databases; scheduled runs write to
production only.

## Run the tests

The parsing logic is a pure function tested against a captured API response, so
this needs no database:

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
python -m pytest tests/ -v
```

To run the poller itself, apply `sql/init/001_carbon_intensity_forecast.sql` to
any PostgreSQL database, copy `.env.example` to `.env` and fill it in, then:

```bash
python -m ingestion.carbon_intensity.forecast_poller
```

## Repository layout

```
ingestion/      Python ingestion package (bind-mounted by Airflow)
dags/           Airflow DAG definitions
sql/            Raw-layer DDL, applied manually per database
dbt/            dbt project (from Phase 1)
dbt_profiles/   dbt connection profile, credentials via env_var()
tests/          pytest suite for the ingestion code
.github/        CI workflow
```

## Working practice

- Every change arrives as a pull request, with CI passing.
- The raw layer is append-only. Nothing in it is updated or deleted.

## Status

**Phase 0 — gates**

- [x] Development and production databases provisioned
- [x] Forecast poller written, with tests
- [x] Forecast archive running on a schedule
- [ ] Endpoint shapes confirmed against the live API
- [ ] Hour-zero asymmetry test
- [ ] dbt project initialised
