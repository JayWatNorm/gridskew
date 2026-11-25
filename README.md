# gridskew

[![CI](https://github.com/JayWatNorm/gridskew/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/JayWatNorm/gridskew/actions/workflows/ci.yml)

Measuring GB electricity grid shortfalls and their effect on carbon intensity
forecast error.

GridSkew is a working data platform built around a real analytical question,
not a tutorial dataset. It applies Python ingestion, PostgreSQL, dbt and Airflow
to live public APIs and keeps every forecast version and every settlement run
it captures, which those APIs later overwrite.

**Results:** <https://jaywatnorm.github.io/gridskew>

**Status:** the analysis is complete (25 November 2026). The Airflow DAGs
keep collecting data, and the nightly dbt job keeps building and testing it.

## What it found

The [results page](https://jaywatnorm.github.io/gridskew) gives every figure
with its method, data window and saved output.

- **Wind strays furthest from its own plan.** With no NESO instruction in
  force, the median wind farm missed its plan by 6.3 to 8.0% of its capacity
  in every whole month from September 2025 to August 2026; the median unit of
  every other type, by under 3.5% ([Q23](https://jaywatnorm.github.io/gridskew/#q23)).
- **Most under-delivery comes with no published outage.** About a quarter
  (24%, 22 August 2025 to 27 September 2026) came with one; for wind, about
  a tenth ([Q5](https://jaywatnorm.github.io/gridskew/#q5)).
- **NESO flags almost all the energy its instructions move at wind farms as
  system actions**, possibly for network reasons: 97% over the settlement
  months August 2025 to September 2026 ([Q24](https://jaywatnorm.github.io/gridskew/#q24)).
- **The carbon forecast's misses only slightly track under-delivery, and
  barely track misses in NESO's demand and wind forecasts.** In the fifth
  of half-hours with the biggest carbon misses (22 August 2025 to 16 August
  2026), under-delivery was only 5% larger than in the fifth with the
  smallest (90% range 3% to 8%). Over the same dates, misses in NESO's
  demand and wind forecasts have rank correlations of only 0.04 and 0.01
  with the size of the carbon miss ([Q28](https://jaywatnorm.github.io/gridskew/#q28),
  [Q8](https://jaywatnorm.github.io/gridskew/#q8), [Q30](https://jaywatnorm.github.io/gridskew/#q30)).
- **After 10 September 2026, up to 12 November, the carbon forecast was
  revised upward as each half-hour approached.** Over the whole window from
  19 August 2026 (2,910 half-hours) the middle change from first to final
  forecast was +19 g/kWh, and a day ahead the forecast ran 21 g/kWh below
  NESO's outturn estimate (2,864 half-hours); the accuracy figures point the
  other way on the 365 earliest half-hours (19 August to 4 September 2026)
  ([Q1](https://jaywatnorm.github.io/gridskew/#q1), [Q10](https://jaywatnorm.github.io/gridskew/#q10)).

The carbon forecast set against under-delivery, demand and wind is the stored
forecast, not a day-ahead one.

## The question

1. **Find the shortfalls.** Where did generating units commit to produce power
   and then not produce it? Compare Physical Notifications (`PN`) with actual
   metered output (`B1610`) per unit and half-hour settlement period.
2. **Measure the carbon impact.** Join those shortfalls to national carbon
   intensity forecast error for the same periods.
3. **Explain them.** Test planned and unplanned outages (`REMIT`), demand
   forecast error and market stress signals as possible explanations.

The [methods](docs/research-questions.md#terms) call the shortfall NESO did not
instruct *unexplained shortfall*; the results page calls its positive part
*under-delivery*.

The analysis stays at unit level because aggregating to fuel type first can
wash out the signal. The shortfall table covers every registered unit with a
published fuel type, interconnectors and pumped storage included; each result
states which units it counts. A unit without a published fuel type has no
physical baseline.

A related question runs independently: **how does a forecast for a given
half-hour change as that half-hour approaches?** This can be answered without
waiting for Elexon settlement data.

## The mechanism behind the question

If committed generation fails, gas peakers may fill the gap at short notice.
Because gas is dirtier than much of what it replaces, two effects would follow:

1. Actual carbon intensity should exceed the published forecast more often
   than it falls below it.
2. Forecasts should revise upwards as the target period approaches.

No result measures the first effect, which is about how often
the outturn is above the forecast. Two results give the average miss
instead, a bias and not a share above or below. Over 17,280 half-hours
(22 August 2025 to 16 August 2026) the stored forecast's misses have a mean
size of 9.3 g/kWh with almost no overall bias (mean −0.6 g/kWh;
[Q29](https://jaywatnorm.github.io/gridskew/#q29)). The stored forecast is a late revision, so this says
little about the mechanism. On the project's own forecast archive, over
2,864 half-hours from 19 August to 12 November 2026, NESO's outturn estimate
was on average 21 g/kWh above the forecast made about a day ahead and
3 g/kWh above the one made about half an hour ahead
([Q10](https://jaywatnorm.github.io/gridskew/#q10)).

The archive measures the second. Over 2,910 half-hours from
19 August to 12 November 2026 the middle change from first to final forecast
was +19 g/kWh, and 72.6% of the changes that were not zero were upward
([Q1](https://jaywatnorm.github.io/gridskew/#q1)); on the 2,530 half-hours after 10 September 2026 it was
+23 g/kWh. Q10's bias changes sign inside the window: negative on its 365
earliest half-hours (19 August to 4 September 2026), positive on the 2,499
from 11 September. That points to a reversal for Q1 on the earliest
half-hours, for which Q1's own output has no figure. The split falls across
a week-long gap in the sample as well as a change in the archive's poll
schedule on 10 September 2026. The window holds no winter or spring. Neither
result shows why the forecast moves, so neither shows the mechanism.

## Why archive the forecasts?

The NESO Carbon Intensity API recalculates its 48-hour forecast every 30
minutes and overwrites the previous value. It cannot later show what it
predicted 48 hours before an event.

GridSkew polls the forecast every 30 minutes and stores every revision with its
retrieval time. This creates a forecast trajectory that the source does not
retain. The archive only extends forwards from the day collection started;
missed revisions cannot be recovered.

## Limitations

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
- Weather data is not used; it could only approximate the inputs used by the
  national forecast model.

## Data and architecture

| Source | Role |
|---|---|
| **Elexon Insights (the BMRS API)** | Physical notifications, metered BM-unit energy, balancing instructions, balancing services volume, outage notices and the BM-unit registry |
| **Elexon Insights, demand** | NESO's national demand forecast (`NDF`) and initial outturn (`INDO`), a one-off back-fill of a year |
| **Elexon Insights, wind** | NESO's wind generation forecast (`WINDFOR`) and metered wind outturn (`FUELHH`), daily beside a year's back-fill |
| **NESO Carbon Intensity API** | Half-hourly national carbon intensity forecasts and outturn |

The platform uses a medallion structure inside PostgreSQL:

| Layer | Implementation | Purpose |
|---|---|---|
| **Bronze** | `raw` schema | Append-only source-grain records and retrieval context; rejected payloads, whose rows are only marked resolved |
| **Silver** | `dbt/models/staging/` and `dbt/models/intermediate/` | Source-conformed records, joins and business logic |
| **Gold** | `dbt/models/marts/` | Terminal facts, dimensions and aggregates at business grain |

Bronze is a database schema rather than object storage because this project
does not need a lake. Its essential rule is that source records are never
updated or deleted: a revision arrives as another row. This makes captured
forecast and settlement revisions observable.

The BM-unit registry is captured in production. dbt exposes its current state
and snapshots the changes observed since collection began. Null fuels remain
unknown; snapshot dates are observation dates.

Two version-controlled dbt seeds provide small reference lookups:
settlement-run order and Elexon's published fuel codes grouped by code
meaning. The fuel-code groups include technology and interconnector roles;
they do not establish a unit's actual fuel or emissions. See the
[dbt guide](dbt/README.md) for their contracts and load commands.

The stack is Python ingestion → PostgreSQL → dbt → Airflow on a self-hosted
Linux server, with separate development and production databases. Thirteen
Airflow DAGs collect carbon intensity forecasts and outturn, `PN`, `QPN`,
three `B1610` settlement runs (`II`, `SF` and `RF`), the BM unit registry,
balancing instructions (`BOALF`), outage notices (`REMIT`, hourly, with a
daily re-read of the last seven days), balancing services volume (`QAS`,
ABSVD) and wind forecast and outturn (`WINDFOR`, `FUELHH`). Two more run
dbt: hourly source-freshness checks, and a nightly job that updates the
period tables and runs the project's data tests.

The sources use different scheduling and backfill strategies because their
time behaviour differs. See
[ingestion patterns](docs/sources/ingestion-patterns.md) for the design.

## Data sources and licences

- Contains BMRS data © Elexon Limited copyright and database right 2026.
  Used under the [BMRS open data licence](https://www.elexon.co.uk/bsc/data/balancing-mechanism-reporting-agent/copyright-licence-bmrs-data/).
- Carbon intensity data from the NESO Carbon Intensity API
  ([carbonintensity.org.uk](https://carbonintensity.org.uk/)), licensed under
  [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/); modified:
  aggregated by GridSkew.
- Historic generation mix from the
  [NESO Data Portal](https://www.neso.energy/data-portal/historic-generation-mix),
  Supported by National Energy SO Open Data, under the
  [NESO Open Data Licence v1.0](https://www.neso.energy/data-portal/neso-open-licence).

Neither provider endorses this project.

## Licence

The code is under the [MIT licence](LICENSE). The data in `docs/results/` and
on the results page stays under its providers' licences, listed above.

## Reliability and validation

Each ingested endpoint has an explicit field contract. Shared validation and
routing report missing, null, incompatible and unexpected fields before typed
parsing; Carbon contracts also validate the nested `intensity` object. Runtime
validation and quarantine are deployed for PN, QPN, B1610, BOALF, REMIT, QAS,
the NDF and INDO back-fill, WINDFOR, FUELHH and both Carbon datasets. Carbon
also checks that each response covers its periods completely.
The BM-unit registry uses a complete-response gate: a rejected or suspiciously
small response publishes no successful extract.

For those twelve datasets, compatible rows continue to typed loading.
Rejected rows are committed to `raw.endpoint_quarantine` with their request
context and complete payload;
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

CI runs these tests, plus linting, the dbt build and the database checks, on
every pull request and every push to `main`.

The dbt project has separate setup and test instructions in
[dbt/README.md](dbt/README.md). The checks CI runs against its throwaway
database are scripts under `tests/integration/`; the live-API explorations
are under `tests/adhoc/`. pytest collects neither.

Much of the code supports the results rather than the pipeline. The chart
script renders every figure on the results page from the saved outputs, and
its tests check each figure's values and the page's text against them. CI
checks the arithmetic of the Q1, Q10, Q25, Q8, Q28, Q29 and Q30 analyses on
hand-worked rows, and every analysis compiles against the test database.

## Repository guide

```text
ingestion/      Python ingestion package
dags/           Airflow DAG definitions
sql/            Raw-layer migrations and provisioning scripts
dbt/            Sources, seeds, models, tests and macros
dbt_profiles/   Connection profile using environment variables
tests/          pytest suite, captured fixtures, CI database checks and ad-hoc checks
scripts/        Chart script for the results, run by hand
site/           Static results page, published with GitHub Pages
docs/           Source, ingestion and deployment documentation
docs/results/   Saved results, with each chart's PNG and Vega-Lite spec
.github/        Continuous-integration and Pages workflows
```

- [Documentation index](docs/README.md)
- [Ingestion patterns](docs/sources/ingestion-patterns.md)
- [Endpoint validation](docs/sources/endpoint-validation.md)
- [Design decisions](docs/design-decisions.md)
- [Operations](docs/operations.md)
- [Homelab CD overview](docs/deployment.md)

## Deployment

The Airflow instance is maintained in a separate, private homelab repository,
which mounts this project's DAG, ingestion and dbt directories. A release is
a workflow in that repository, dispatched by hand with the exact commit of
each repository, and it needs a passing CI run on the GridSkew commit. It
pauses the affected DAGs and lets their runs drain, updates both host
checkouts, verifies Airflow and restores the schedules.

The [homelab CD overview](docs/deployment.md) explains the release boundary.
Host SQL, Airflow pools and the observed rollout sequence are in that
repository's release runbook, which is not public.

## Status

The analysis is complete (25 November 2026). The DAGs keep collecting data,
and the nightly dbt job keeps building and testing it.

**Complete and running**

- Carbon intensity forecast and outturn collection
- `PN`, `QPN`, `B1610`, `BOALF`, `REMIT`, `QAS` and wind ingestion, backfills
  and scheduled Airflow runs; a one-off demand back-fill
- Append-only source tables in `raw`, with a loader role that can insert and
  read but not update or delete; quarantine rows are only marked resolved,
  by the owner
- Runtime validation and quarantine for the twelve time-series datasets, plus
  complete-response validation for the BM-unit registry
- Carbon forecast and outturn period-completeness checks
- dbt sources, production freshness checks and fourteen source-grain staging
  views
- Settlement-period conversion covering normal days and UK clock changes
- BM unit registry capture, current dimension and observed-history snapshot
- Pull-request CI with Python tests, linting, DAG compilation, `dbt parse`,
  a deterministic fixture-backed `dbt build` and the nightly dbt commands run
  against that build
- A nightly dbt job on named selectors: it runs the project's data tests
  without recreating a view, records each node's result in a table and
  publishes dbt's manifest and run results

**Period facts**

- Two private incremental period tables, `int_elexon__b1610_period` and
  `int_elexon__pn_period_mwh`, maintain metered and committed energy.
  `fct_generation` and `fct_commitments` expose those values as views joined
  to current registry evidence.
- Two more private incremental tables carry the shortfall question:
  `int_elexon__instruction_intervals` holds, for each half-hour, the
  stretches in which a bid-offer acceptance was in force, and
  `int_shortfall_by_unit_period` compares each registry unit with a fuel
  type against its expected output, the PN with the instructed level in its
  place for those stretches. It also carries ABSVD (`QAS`), the energy of
  balancing services outside the mechanism. The analyses that read it are
  listed in the [dbt guide](dbt/README.md).

**Read**

- The [results page](https://jaywatnorm.github.io/gridskew) holds every
  result, with its chart, method and saved output; the headline
  results are under [What it found](#what-it-found). Each method is in
  [research questions](docs/research-questions.md).
- Four views trace each half-hour's carbon forecast from its first to its
  final version; see
  [Carbon forecast trajectory](docs/carbon-forecast-trajectory.md).
- Q1 and Q10, how the carbon forecast is revised as a half-hour approaches
  and how accurate it is at each lead time, were read on
  22 November 2026, on 2,910 and 2,864 half-hours from 19 August to
  12 November 2026 ([Q1](https://jaywatnorm.github.io/gridskew/#q1), [Q10](https://jaywatnorm.github.io/gridskew/#q10)).

System prices and weather are not used.

The shortfall does not subtract `QPN` from PN; the
[QPN dataset notes](docs/sources/elexon/015_qpn.md) explain why.
