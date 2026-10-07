# gridskew

Measuring GB electricity grid shortfalls and their effect on carbon intensity
forecast error.

GridSkew is a working data platform built around a real analytical question,
not a tutorial dataset. It applies Python ingestion, PostgreSQL, dbt and Airflow
to live public APIs and preserves the revisions that those APIs overwrite.

## The question

1. **Find the shortfalls.** Where did generating units commit to produce power
   and then not produce it? Compare Physical Notifications (`PN`) with actual
   metered output (`B1610`) per unit and half-hour settlement period.
2. **Measure the carbon impact.** Join those shortfalls to national carbon
   intensity forecast error for the same periods.
3. **Explain them.** Test planned and unplanned outages (`REMIT`), demand
   forecast error and market stress signals as possible explanations.

The analysis stays at unit level because aggregating to fuel type first can
wash out the signal. It initially covers physical generators such as CCGT,
nuclear and wind, for which `PN` is a meaningful physical baseline.

A related question runs independently: **how does a forecast for a given
half-hour change as that half-hour approaches?** This can be answered without
waiting for Elexon settlement data.

## The mechanism being tested

If committed generation fails, gas peakers may fill the gap at short notice.
Because gas is dirtier than much of what it replaces, two predictions follow:

1. Actual carbon intensity should exceed the published forecast more often
   than it falls below it.
2. Forecasts should revise upwards as the target period approaches.

The first prediction was tested on 19 August 2026 and **was not supported**.
Across 17,522 half-hour periods, the median error was -1 gCO2/kWh and actual
intensity exceeded forecast 47.5% of the time, excluding ties.

That is a weak test of the underlying mechanism because the API's historical
forecast is a late revision. The second prediction is what the project's own
forecast archive is designed to test.

## Why archive the forecasts?

The NESO Carbon Intensity API recalculates its 48-hour forecast every 30
minutes and overwrites the previous value. It cannot later show what it
predicted 48 hours before an event.

GridSkew polls the forecast every 30 minutes and stores every revision with its
retrieval time. This creates a forecast trajectory that the source does not
retain. The archive only extends forwards from the day collection started;
missed revisions cannot be recovered.

## Honest limitations

- Published historical forecast error is attenuated because the stored
  forecast is a late revision.
- Forecast revisions also reflect weather, demand and interconnector updates;
  attributing them to generation shortfalls requires the per-unit Elexon join.
- `B1610` includes metered BM-unit imports and exports, not only physical
  generation. Shortfall and fuel comparisons need a verified generator cohort;
  units without a supported fuel label remain unknown.
- Per-unit metered data is published about five working days after the event
  and is later restated, so the full analysis trails real time.
- The project measures associations and accounting relationships, not causal
  proof.
- Weather data planned for a later phase is a proxy for the inputs used by the
  national forecast model.

## Data and architecture

| Source | Role |
|---|---|
| **Elexon Insights** | Physical notifications and metered BM-unit energy; later phases add outages, balancing actions, demand and prices |
| **NESO Carbon Intensity API** | Half-hourly national carbon intensity forecasts and outturn |

The platform uses a medallion structure inside PostgreSQL:

| Layer | Implementation | Purpose |
|---|---|---|
| **Bronze** | `raw` schema | Append-only source-grain records, retrieval context and rejected payloads |
| **Silver** | dbt `staging/` and `intermediate/` | Source-conformed records, joins and business logic |
| **Gold** | dbt `marts/` | Terminal facts, dimensions and aggregates at business grain |

Bronze is a database schema rather than object storage because this project
does not need a lake. Its essential rule is that source records are never
updated or deleted: a revision arrives as another row. This makes captured
forecast and settlement revisions observable.

The BM-unit registry is captured in production. dbt exposes its current state
and snapshots the changes observed since collection began. Null fuels remain
unknown; snapshot dates are observation dates.

Three version-controlled dbt seeds provide small reference lookups:
settlement-run order, Elexon's published fuel codes grouped by code meaning,
and the five Carbon Intensity labels ordered from very low to very high. The
fuel-code groups include technology and interconnector roles; they do not
establish a unit's actual fuel or emissions. The Carbon labels do not encode
fixed numerical thresholds. See the [dbt guide](dbt/README.md) for their
contracts and load commands.

The stack is Python ingestion → PostgreSQL → dbt → Airflow on a self-hosted
Linux server, with separate development and production databases. Eight
Airflow DAGs collect carbon intensity forecasts and outturn, `PN`, `QPN`, two
standing `B1610` settlement runs, a bounded `B1610` R1 capture and the BM unit
registry. Two more run dbt:
source-freshness checks configured hourly and the nightly S6 period models.
At the 21 September 2026 checkpoint, the raw layer contained about 247 million
rows.

The sources use different scheduling and backfill strategies because their
time behaviour differs. See
[ingestion patterns](docs/sources/ingestion-patterns.md) for the design.

## Data sources and licences

- Contains BMRS data © Elexon Limited copyright and database right 2026.
  Used under the [BMRS open data licence](https://www.elexon.co.uk/data/balancing-mechanism-reporting-agent/copyright-licence-bmrs-data/).
- Carbon intensity data from the NESO Carbon Intensity API
  ([carbonintensity.org.uk](https://carbonintensity.org.uk/)), licensed under
  [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).

Neither provider endorses this project.

## Reliability and validation

Each ingested endpoint has an explicit field contract. Shared validation and
routing report missing, null, incompatible and unexpected fields before typed
parsing; Carbon contracts also validate the nested `intensity` object. Runtime
validation and quarantine are deployed for PN, QPN, B1610 and both Carbon
datasets. Carbon also checks that each response covers its periods completely.
The BM-unit registry uses a complete-response gate: a rejected or suspiciously
small response publishes no successful extract.

For the five PN/QPN/B1610 and Carbon datasets, compatible rows continue to
typed loading. Rejected rows are committed to
`raw.endpoint_quarantine` with their request context and complete payload;
warning-only rows remain loadable and produce grouped logs. Tests cover the
contracts, routing, response envelopes, quarantine evidence and loader wiring
without calling live services. See
[endpoint validation](docs/sources/endpoint-validation.md).

## Tests

The Python suite uses captured API fixtures and makes no live API or database
calls:

```bash
pip install -r requirements-dev.txt
python -m pytest
```

The dbt project has separate setup and test instructions in
[dbt/README.md](dbt/README.md). Scripts under `tests/adhoc/` are excluded from
pytest; some are checks that CI runs.

## Repository guide

```text
ingestion/      Python ingestion package
dags/           Airflow DAG definitions
sql/            Raw-layer DDL
dbt/            Sources, seeds, models, tests and macros
dbt_profiles/   Connection profile using environment variables
tests/          pytest suite, captured fixtures and ad-hoc checks
docs/           Source, ingestion and deployment documentation
.github/        Continuous-integration workflow
```

- [Data source documentation](docs/README.md)
- [Ingestion patterns](docs/sources/ingestion-patterns.md)
- [Endpoint validation](docs/sources/endpoint-validation.md)
- [Homelab CD overview](docs/deployment.md)

## Deployment

The Airflow instance is maintained in a separate homelab repository. The
project's ingestion package is bind-mounted, while DAG files are copied into
the shared scheduler repository. Deployment therefore requires both artefacts
to be updated; it is not only a pull of this repository.

The [homelab CD overview](docs/deployment.md) explains the release boundary.
Host SQL, Airflow pools and the observed rollout sequence are documented in
`homelab-platform/docs/gridskew-release.md` in the platform checkout.

## Status

**Complete and running**

- Carbon intensity forecast and outturn collection
- `PN`, `QPN` and `B1610` ingestion, backfills and scheduled Airflow runs
- Append-only raw storage
- Runtime validation and quarantine for the five time-series datasets, plus
  complete-response validation for the BM-unit registry
- Carbon forecast and outturn period-completeness checks
- dbt sources, production freshness checks and six source-grain staging views
- Settlement-period conversion covering normal days and UK clock changes
- BM unit registry capture, current dimension and observed-history snapshot
- Pull-request CI with Python tests, linting, DAG compilation, `dbt parse`
  and a deterministic fixture-backed `dbt build`

**Released 26 September 2026**

- Two private incremental period tables, `int_elexon__b1610_period` and
  `int_elexon__pn_period_mwh`, maintain metered and committed energy.
  `fct_generation` and `fct_commitments` expose those values as views joined
  to current registry evidence.
- `elexon_settlement_run_codes` supplies settlement-run ordering and
  `elexon_fuel_codes` supports registry validation.

**Built, result pending**

- Four views trace each half-hour's carbon forecast from its first to its
  final version under a reading rule fixed in advance. The drift result is
  read once, when 60 days of complete trajectories exist; see
  [Carbon forecast trajectory](docs/carbon-forecast-trajectory.md).

**Available reference data**

- Three version-controlled seeds have explicit PostgreSQL types and data tests.
  `carbon_intensity_bands` provides label ordering; no current model consumes
  it.

**Next**

- First results from data already held: how carbon-intensity forecasts drift
  as a period approaches, how forecast accuracy changes with lead time, and a
  check that settlement revisions do not change the headline
- A bounded capture of the R1 settlement run, scheduled for 5–11 October, to
  measure how metered output is revised
- Balancing instructions (`BOALF`) and outage notices (`REMIT`), so shortfall
  can be separated into instructed and residual parts
- Airflow 3 upgrade (October to early November)

Demand, system prices and weather remain later extensions.

`QPN` does not alter the settlement-shortfall calculation. The model compares
integrated `PN MWh` with `B1610 MWh` without subtracting QPN; QPN remains
available for Dynamic Data comparisons. The completed one-unit comparison
supports this rule: only 18,300 of 41.5 million observed QPN rows were non-zero,
all for one BM unit. See the
[QPN dataset notes](docs/sources/elexon/015_qpn.md).
