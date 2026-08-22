# gridskew
Measuring GB electricity grid shortfalls and their effect on carbon intensity
forecast error.

# Why it exists
Recently passed PCEP, working on dbt knowledge, this is a learning project and 
to keep me sharp subjects and expand my knowledge. I have over a 
decade of SQL, data pipelines and analysis experience but I wish to
push my technical skills around data engineering. 

# AI Disclaimer
AI is used for documentation, coaching, scaffolding, code review & best practice.
Examples may be generated on new subjects to assit with understanding

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

| Source | Used for |
|---|---|
| **Elexon Insights** | Physical notifications, per-unit generation, outage notices, balancing actions, demand, imbalance prices |
| **NESO Carbon Intensity API** | National carbon intensity forecast and outturn, half-hourly |

Both public and free. Phase 1 deliberately uses these two only, with no
cross-organisation joins.

## Stack

Python ingestion → PostgreSQL → dbt → Airflow, on a self-hosted Linux server.
Separate development and production databases; scheduled runs write to
production only.

Four DAGs are live in production: the carbon intensity forecast archive every 30
minutes, the carbon intensity outturn poller daily, and the Elexon `PN` and `QPN`
pollers daily. The outturn poller detects its own window, so it backfills on an
empty table and catches up from the last stored period thereafter. The two Elexon
DAGs take their window from Airflow and backfill through `catchup=True` instead —
[docs/sources/ingestion-patterns.md](docs/sources/ingestion-patterns.md) explains
why the two sources cannot share one approach.

## Run the tests

The parsing logic is a pure function tested against a captured API response, so
this needs no database:

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
python -m pytest tests/ -v
```

To run the pollers themselves, apply the files in `sql/init/` to any PostgreSQL
database, copy `.env.example` to `.env` and fill it in, then:

```bash
python -m ingestion.carbon_intensity.forecast_poller   # 48h ahead forecast, every 30 min
python -m ingestion.carbon_intensity.outturn_poller    # settled actuals, daily
python -m ingestion.elexon.pn_poller                   # physical notifications, one day
python -m ingestion.elexon.qpn_poller                  # quiescent physical notifications, one day
```

The two carbon intensity pollers detect their own window. The Elexon pollers take
their window as arguments and default to yesterday, because Airflow supplies the
window per run — see
[docs/sources/ingestion-patterns.md](docs/sources/ingestion-patterns.md) for why
the two approaches differ.

**One Elexon day is about 132,000 rows for `PN` and 120,000 for `QPN`**, so expect
either to take a minute rather than a second.

## Repository layout

```
ingestion/      Python ingestion package (bind-mounted by Airflow)
dags/           Airflow DAG definitions
sql/            Raw-layer DDL, applied manually per database
dbt/            dbt project (from Phase 1)
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

## Deploying an Elexon DAG

The Elexon DAGs use `catchup=True`, so **unpausing one starts a backfill**. Their
`start_date` is a fixed literal defining how much history to load, currently one
year.

1. Apply the relevant file from `sql/init/` to **both** databases. Confirm the
   key with `\d raw.elexon_pn` — a wrong key surfaces as a constraint violation
   on the first real load, not at deploy time.
2. Push, then pull on the Airflow host. **A pull is not enough on its own.**
   Airflow lives in the separate `homelab-platform` repository and reads DAGs
   from its own shared `dags/` folder, so the DAG file has to be **copied** there:

   ```bash
   cp ~/gridskew/dags/gridskew_elexon_pn_dag.py ~/homelab-platform/dags/
   ```

   The `ingestion/` package is different — Airflow **bind-mounts** it from this
   repository, so a pull is sufficient for poller changes and no copy is needed.
   That asymmetry is the thing to remember: **DAGs are copies, ingestion is
   mounted.**
3. **Wait for the scheduler to notice.** New files are picked up on
   `dag_dir_list_interval` (300s by default), and each file is parsed
   independently rather than as a batch, so two DAGs copied in the same second
   can appear minutes apart and in no particular order. If one is still missing
   after ten minutes, run `airflow dags list-import-errors` in the scheduler
   container.
4. **Leave the DAG paused.** Trigger one run manually and check the row count —
   a PN day is about 132,000 rows across ~2,500 units, of which ~2,450 have a
   null `bm_unit`.
5. Unpause. Runs execute one at a time at `max_active_runs=1`, roughly 1.4
   requests per minute, and a year takes about four hours.

Pausing mid-backfill is safe: the run in flight finishes and unpausing resumes
where it left off. Each run is addressed to a specific day, so nothing is lost.

**Unpause one DAG at a time.** There is no shared Airflow Pool throttling Elexon
requests across DAGs yet, so two concurrent backfills would double the request
rate with nothing coordinating them.

**`start_date` is not maintenance-free.** Standing a DAG up on a fresh Airflow
instance years later re-backfills from that same date, so the window grows with
time. Change the literal deliberately if that is not wanted — **never compute it
from `datetime.now()`**, which breaks Airflow's scheduling. Reasoning in
[docs/sources/ingestion-patterns.md](docs/sources/ingestion-patterns.md).

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

- [x] `PN` raw table, poller and tests
- [x] `PN` Airflow DAG, `catchup=True` over a year
- [x] `QPN` raw table, poller, tests and DAG
- [x] Both Elexon DAGs deployed to production
- [ ] `PN` backfill complete
- [ ] `QPN` backfill complete
- [ ] `B1610` ingestion
- [ ] BM unit registry snapshot
- [ ] dbt project initialised
- [ ] Sources with `freshness` on every raw table
- [ ] Staging models, 1:1 with sources
- [ ] Settlement-period macro, with unit tests
- [ ] Incremental generation and commitment facts

`QPN` was added to the plan after reconnaissance found it declares MW netted off
the `PN`. Whether that deduction belongs in the shortfall calculation is an open
question rather than an assumption — see
[docs/sources/elexon/015_qpn.md](docs/sources/elexon/015_qpn.md).

Counting a full day settled most of it: **50 of 119,600 QPN rows are non-zero,
and they all belong to a single BM unit.** The open question is real but affects
one unit in the market, so it does not block the models. It is ingested anyway,
because the question cannot be answered later without the data.
