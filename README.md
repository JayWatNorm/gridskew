# gridskew
Measuring GB electricity grid shortfalls and their effect on carbon intensity
forecast error.

## Why it exists

A working data platform built around a real question rather than a tutorial
dataset. Over a decade of SQL, data pipelines and analysis sits behind it; the
deliberate focus here is the engineering - orchestration, testing, dimensional
modelling and deployment — against live public APIs with all the awkwardness
that implies.

## AI assistance

AI-assisted tools support coaching, documentation, scaffolding and code review.
The repository owner makes and verifies the design, implementation and analysis
decisions represented here.

---

## The question

1. **Find the shortfalls.** Where did generating units commit to produce power
   and then not produce it? Physical Notifications (`PN`) against actual
   metered output (`B1610`), per unit, per half-hour settlement period.
2. **Measure the carbon impact.** Join those shortfalls to the error in the
   national carbon intensity forecast for the same periods.
3. **Explain them.** Planned versus unplanned outages (`REMIT`), demand
   forecast error, market stress signals.

Unit-level grain throughout. Aggregating to fuel type first washes out the
signal.

A fourth question runs alongside and independent of those three: **how does a
forecast for a given half-hour change as that half-hour approaches?** It needs
no Elexon data and no settlement lag, so it is the first question here that
will have an answer.

## The mechanism being tested

Suppliers commit to output ahead of time, some fail to deliver, and gas peakers
fill the gap at short notice. Gas is dirtier than most of what it replaces.

Two predictions follow.

**1. Forecast error should skew positive.** Actual intensity above forecast
more often than below.

**Tested 2026-08-19. Not supported.** Over a year of half-hourly periods
(n = 17,522) the median error is -1 gCO2/kWh, and actual comes in above
forecast 47.5% of the time excluding ties. If anything the forecast is
slightly conservative.

It's a weak test though. The API's stored forecast is a late revision, made
when the model already knew most of the answer, so it says little about the
48-hour case.

**2. Forecasts should revise upward as the period approaches.** Short-notice
gas is information the model lacks 48 hours out and has 30 minutes out.

Untested. This is what the archive is for: it measures the mechanism directly,
without the attenuation that weakens prediction 1.

## Why this repository archives forecasts

The Carbon Intensity API publishes a 48-hour-ahead forecast and **re-runs its
model every 30 minutes, overwriting the stored value in place.** Ask it today
what it predicted for yesterday afternoon and you get the final revision, made
minutes before the event, when the model already had most of the answer.

So there is no public record of how a forecast changed as it approached. This
project polls every 30 minutes and stores every revision with the timestamp of
the request. **The archive only extends forwards from the day it starts. The
past is not recoverable**, which is why it was the first thing built.

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

| Source | Role in the project |
|---|---|
| **Elexon Insights** | Physical notifications and per-unit generation are ingested; outage notices, balancing actions, demand and imbalance prices are planned explanatory inputs |
| **NESO Carbon Intensity API** | National carbon intensity forecast and outturn, half-hourly |

Both public and free. Phase 1 deliberately uses these two only, with no
cross-organisation joins.

## Architecture

Medallion, in a database rather than a lake:

| Layer | Here | Contains |
|---|---|---|
| **Bronze** | `raw` schema | Source-grain typed records for contracted fields, plus complete rejected payloads in endpoint quarantine. Append-only, with retrieval context |
| **Silver** | dbt `staging/` then `intermediate/` | Cleaned and conformed, then joined and given business logic |
| **Gold** | dbt `marts/` | Facts, dimensions and aggregates at business grain. Terminal — no mart reads another |

Two departures from the canonical description, both deliberate. **Bronze is a
Postgres schema, not files on object storage**, because there is no lake and
227M rows do not need one. And **silver is split in two** — `staging` preserves
source grain and performs only source-conformed, row-level standardisation;
`intermediate` is where joins and grain changes live. This is dbt convention
rather than medallion convention, and the more useful distinction.

The bronze rule that everything else depends on: **nothing in `raw` is ever
updated or deleted.** A revision arrives as a new row. That is what makes
restatement observable, and it is why a source that overwrites in place —
carbon intensity forecasts, Elexon settlement runs — can only be captured
going forwards.

## Stack

Python ingestion → PostgreSQL → dbt → Airflow, on a self-hosted Linux server.
Separate development and production databases; scheduled runs write to
production only.

Six DAGs are live in production: the carbon intensity forecast archive every 30
minutes, the carbon intensity outturn poller daily, `PN` and `QPN` daily, and
two daily `B1610` DAGs that sample the II and SF settlement runs. An empty
outturn table triggers a 365-day backfill; later runs fetch a rolling seven-day
window. `PN`, `QPN` and the `B1610` II DAG take their windows from Airflow and
use `catchup=True`. The `B1610` SF DAG runs forwards only with `catchup=False`.
[docs/sources/ingestion-patterns.md](docs/sources/ingestion-patterns.md) explains
why these sources use different approaches.

## Endpoint validation

PN, QPN and B1610 have explicit field contracts and a shared offline validator.
It checks required fields, nullability and exact Python types, and returns
row-level errors and warnings. The PN poller now validates every fetched row in
the local branch: compatible and warning-only rows continue to typed parsing,
while rejected rows are excluded and passed to the fixed quarantine writer with
their source index, request context and complete payload. Warning-only findings
also produce warning-level JSON logs grouped by reason and affected field, with
the complete affected-row count and up to five source indexes. Tests exercise
the routing, grouped warning evidence and writer boundary without a live API or
database.

This PN integration is not deployed. QPN and B1610 do not yet call the
validator. A mixed PN response commits its compatible rows and rejected-row
evidence before failing the run; an all-rejected response commits quarantine
evidence, skips typed parsing and loading, then fails. Accepted typed rows retain
the contracted fields rather than a blanket copy of every source field. See
[endpoint validation](docs/sources/endpoint-validation.md) for the interfaces,
completed checks and integration boundary.

## Run the tests

The Python tests cover parsing and source-contract validation using captured
API fixtures and controlled input changes. They need no database or live API calls:

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
python -m pytest tests/ -v
```

To run only the endpoint-contract and batch-validator tests:

```bash
python -m pytest tests/test_validation.py -v
```

The dbt unit tests use small inline fixtures to exercise model logic without
reading the production source rows. Run them from the `dbt/` directory:

```powershell
dbt test --select "test_type:unit" --profiles-dir ..\dbt_profiles
```

Four PN staging tests cover normal 48-period days, the spring clock change in
2026 and 2027, the repeated autumn hour, valid day boundaries, out-of-range
periods and null inputs.

PN is the test harness for the shared settlement-period macro. QPN and B1610
pass the same `settlement_date` and `settlement_period` arguments, so repeating
the full fixture matrix would test identical logic. The combined three-model
build verifies that each staging model integrates the macro successfully.

To run the pollers themselves, apply the files in `sql/init/` to any PostgreSQL
database, copy `.env.example` to `.env` and fill it in, then:

```bash
python -m ingestion.carbon_intensity.forecast_poller   # 48h ahead forecast, every 30 min
python -m ingestion.carbon_intensity.outturn_poller    # settled actuals, daily
python -m ingestion.elexon.pn_poller                   # physical notifications, one day
python -m ingestion.elexon.qpn_poller                  # quiescent physical notifications, one day
python -m ingestion.elexon.b1610_poller                # metered volumes, one day
```

The two carbon intensity pollers detect their own window. The Elexon pollers
accept their window as arguments and provide a manual-run default. Airflow
supplies the window and, for B1610, the settlement lag per run — see
[docs/sources/ingestion-patterns.md](docs/sources/ingestion-patterns.md) for why
the two approaches differ.

**One Elexon day averages about 126,000 rows for `PN` and 113,000 for `QPN`**, so
expect either to take a minute rather than a second.

## Repository layout

```
ingestion/      Python ingestion package (bind-mounted by Airflow)
dags/           Airflow DAG definitions
sql/            Raw-layer DDL, applied manually per database
dbt/            dbt sources, staging models, inline unit tests and reusable macros
dbt_profiles/   dbt connection profile, credentials via env_var()
tests/          pytest suite, captured fixtures, and ad-hoc data checks
docs/           Source and dataset documentation
.github/        CI workflow
```

`tests/adhoc/` holds exploratory scripts that call the live API. They are
excluded from pytest deliberately — see
[tests/adhoc/README.md](tests/adhoc/README.md).

## Working practice

- Every change arrives as a pull request, with CI passing.
- The raw layer is append-only. Nothing in it is updated or deleted.

## Deployment

The DAGs are written to run on an Airflow instance that lives outside this
repository and is shared with other projects, so deploying them is not a `git
pull`. DAG files are copied into the scheduler's folder; the `ingestion/`
package is bind-mounted from a checkout.

The PN, QPN and B1610 II DAGs use `catchup=True`, so unpausing one starts a
backfill of whatever history its `start_date` defines. The B1610 SF DAG uses
`catchup=False` and runs forwards only.

Full sequence, settings and pitfalls: **[docs/deployment.md](docs/deployment.md)**.

## Status

**Phase 0: gates, complete**

- [x] Development and production databases provisioned
- [x] Forecast poller written, with tests
- [x] Outturn poller written, with tests
- [x] Both pollers deployed to Airflow, running on schedule against production
- [x] Endpoint shapes confirmed against the live API
- [x] Elexon rate behaviour measured empirically
- [x] Dataset documentation, with a captured fixture per dataset
- [x] CI running on every pull request
- [x] Hour-zero asymmetry test, pre-registered and demoted on its own rule

**Phase 1: the spine**

- [x] `PN` raw table, poller, tests and DAG
- [x] `QPN` raw table, poller, tests and DAG
- [x] `PN` and `QPN` DAGs deployed to production
- [x] `PN` backfill complete
- [x] `QPN` backfill complete
- [x] `B1610` raw table, poller, tests and DAGs
- [x] `B1610` II backfill complete; SF running forwards
- [x] Local PN validation routing, warning evidence, quarantine and
      fail-after-commit task status (not deployed)
- [ ] BM unit registry snapshot
- [x] dbt project initialised
- [x] Sources declared for all five raw tables, with freshness checks
- [x] Staging models preserve source grain
- [x] Settlement-period conversion integrated into `PN`, `QPN` and `B1610`
      staging as `period_start_utc`
- [x] Settlement-period unit-test coverage: four fixture-backed PN tests cover
      normal, spring, autumn, invalid-period, null-input and future-date cases
- [ ] Incremental generation and commitment facts

`QPN` was added to the plan after reconnaissance found it declares MW netted off
the `PN`. Whether that deduction belongs in the shortfall calculation is an open
question rather than an assumption — see
[docs/sources/elexon/015_qpn.md](docs/sources/elexon/015_qpn.md).

A full year of data bounds it: **18,300 of 41.5M QPN rows are non-zero — 0.04% —
and every one belongs to a single BM unit.** The question is real but cannot
affect more than one unit's figures, so it does not block the models. QPN is
ingested regardless, because the question is unanswerable without the data.
