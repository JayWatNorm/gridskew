# gridskew dbt project

This project transforms raw API captures and owns small, version-controlled
reference datasets. It holds source-grain staging models, a BM-unit current
dimension with an observed-history snapshot, incremental period tables for
metered (B1610) and committed (PN) energy exposed as the `fct_generation` and
`fct_commitments` views, and private models that prepare the research
questions.

**`dbt_dev` is in the production database and is not disposable.** The local
profile reads `gridskew_prod.raw` and writes to `dbt_dev`; the deployed
BM-unit and nightly jobs select the homelab `dev` target in the same database,
and `dbt_dev_snapshots` holds observed registry history. Freshness uses the
separate `prod` target. Verify the effective database, target, schema and user
before running a command that creates or updates relations. Use a full
`dbt build` only against a verified disposable database containing fixtures.

## Project structure

| Path | Purpose |
|---|---|
| `models/staging/` | Source-grain models with explicit columns, standard names and row-level normalisation |
| `models/intermediate/` | Joins, grain changes and reusable business logic |
| `models/marts/` | Facts, dimensions and final aggregates |
| `snapshots/` | Observed BM-unit attribute history from the first successful capture |
| `macros/` | Reusable SQL expressions, including settlement-period conversion |
| `seeds/` | Small reference datasets, their explicit types, documentation and data tests |
| `models/*/*/_*__unit_tests.yml` | Inline mock inputs and expected results for dbt unit tests |
| `analyses/` | Queries that read the models and that no job runs |
| `selectors.yml` | The named selections the nightly job runs |
| `../dbt_profiles/` | Local profile; credentials come from environment variables |

Models are materialised as views unless a model defines a different strategy.
Staging models must not aggregate or deduplicate source rows, and join only
to attach a capture's own metadata (`stg_elexon__bm_units` reads its
extract's retrieval time from the manifest).

## BM-unit registry lineage

The poller commits each complete response to
`raw.elexon_bm_units_extracts` and `raw.elexon_bm_units` in one transaction.
The raw grain is `(extract_id, source_index)`: one National Grid BM-unit ID can
have multiple source rows with distinct EICs. The manifest stores separate raw
row and distinct-unit counts. Rejected or suspiciously small responses do not
get a successful manifest, so the last good state stays current.

`stg_elexon__bm_units` preserves every accepted raw row and converts blank
capacity strings to null numeric MW. `int_elexon__bm_units_current` selects the
latest successful manifest and combines a unit's sorted distinct EICs after
checking its other attributes agree. `dim_bm_unit` exposes one current row per
National Grid ID. It preserves null fuel values; no unit name or identifier is
used to infer fuel or physical generation. A scoped relationship test checks
populated fuel codes against `elexon_fuel_codes`. It does not require historical
PN or B1610 IDs to occur in today's registry.

`snap_elexon__bm_units` uses dbt's `check` strategy to create a new version
when a business attribute changes. `hard_deletes: invalidate` closes a version
when a unit disappears from a complete extract; a later reappearance opens a
new version. Polling metadata is excluded from the change comparison. The
validity dates are when GridSkew observed a state, not Elexon's business
effective dates. The endpoint provides no pre-collection attribute history.

The deployed daily Airflow DAG captures a complete extract, loads
`elexon_fuel_codes`, runs `dbt test --select +dim_bm_unit` and the three
registry singular tests, then runs the snapshot. It rechecks the manifest
immediately before snapshotting. It tests existing views rather than rebuilding
them, because recreating upstream views can drop their dependants.

Use the guarded Airflow task for live retries. Do not replace its tests with
`dbt build --select +dim_bm_unit` as a scheduled or standalone repair.

Schema creation, grants and structural changes follow the administrator-run
SQL and observed release sequence in
`homelab-platform/docs/gridskew-release.md` in the platform checkout. The
[homelab CD overview](../docs/deployment.md) explains that boundary. Do not
create a fresh snapshot over existing production history.

## Seeds

`dbt seed` loads each CSV as a physical table in the target schema. The three
seeds are:

| Seed | Grain and purpose |
|---|---|
| `elexon_settlement_run_codes` | One row per settlement-run code, ordering II → SF → R1 → R2 → R3 → RF |
| `elexon_fuel_codes` | One row per published Elexon fuel-type code, grouped by code meaning |
| `carbon_intensity_bands` | One row per API index label, ordered from very low to very high |

The CSVs are version-controlled sources of truth; their `dbt_dev` tables are
reloadable copies. `elexon_fuel_codes.code_group` includes plant technologies,
storage and interconnectors, so it does not verify an individual BM unit's
fuel, renewable status or emissions. `INTELE` remains unclassified because
its published meaning is unresolved. The Carbon Intensity seed stores label
order only, not fixed gCO2/kWh boundaries.

## Settlement periods

`macros/settlement_period.sql` converts a British-local settlement date and
period into a UTC instant using PostgreSQL's `Europe/London` timezone rules. It
also derives whether the date contains 46, 48 or 50 periods. `PN`, `QPN` and
`B1610` staging expose the result as `period_start_utc`. Four fixture-backed PN
unit tests cover normal 48-period days, the 2026 and 2027 spring boundaries,
the 2026 repeated autumn hour, valid day limits, out-of-range periods and null
inputs.

PN is the test harness for the shared macro. QPN and B1610 call it with the
same `settlement_date` and `settlement_period` arguments, so they do not repeat
the same fixture matrix. The combined three-model build verifies their macro
integration. A consumer-specific unit test belongs with either model if its
input handling later diverges through casting, renaming or filtering.

## Period facts

The four incremental tables recompute only settlement periods with new
captures: the two period tables that feed the fact views, and the two
shortfall tables below them.

The following is the normal nightly command sequence for reference. Live runs
use the guarded Airflow task in the shared `gridskew_dbt` pool; these commands
alone do not supply its missing-table guard, connection checks or serialisation:

```powershell
dbt test --selector nightly_run_code_check
dbt run --selector nightly_models
dbt test --selector nightly_tests
```

| Selector | Selects |
|---|---|
| `nightly_run_code_check` | The generic tests of `stg_elexon__b1610`; an unknown settlement-run code stops the job before a table is written |
| `nightly_models` | Every incremental model: the two period tables, then the instruction intervals and the shortfall table |
| `nightly_tests` | Every data test except those tagged `full_population` and any test of the BM-unit snapshot |

The job never recreates a view. `nightly_tests` covers the sources, the
staging and intermediate views, the registry dimension and the seeds, so
balancing instructions, outage notices and the carbon models are tested every
night. Tests tagged `full_population` re-read all history and run with a full
refresh. Unit tests run in CI: they check model logic against fixed rows, not
the stored data.

The nightly DAG refuses to start a scheduled run while a period table is
missing, and `PERIOD_TABLES` in the DAG names the tables it requires. A new
incremental model is added to that list in the same change; CI fails
otherwise.

A manual load outside the capture-time margin, a raw edit, a settlement-run
seed change or a model-logic change requires an observed full refresh. Trigger `gridskew__dbt_nightly` with `full_refresh=true` through
the platform runbook. Its source/run-code checks block before the write, and
its descendant build restores the fact views. Coordinate the shared dbt pool,
verify all descendants and tests afterwards, and follow the runbook's recovery
sequence if a cascade fails. A standalone full-refresh command omits those
controls.

See `homelab-platform/docs/gridskew-release.md` in the platform checkout.

## Node results and artifacts

The project's `on-run-end` hook calls the `record_run_results` macro. An
invocation that passes `--vars '{audit: true}'` writes one row per node to
`dbt_dev.dbt_node_results`; any other invocation writes nothing. The nightly
job passes the variable on each of its commands.

| Column | Holds |
|---|---|
| `invocation_id` | dbt's ID for one command. Part of the key |
| `airflow_run_id` | The Airflow run that started the command; null for a run from a workstation |
| `dbt_command` | `run`, `test` or `build` |
| `node_id` | dbt's unique ID of the model, test, seed or snapshot. Part of the key |
| `resource_type` | `model`, `test`, `seed` or `snapshot` |
| `status` | dbt's result: `success`, `pass`, `warn`, `fail`, `error` or `skipped` |
| `execution_seconds` | Time dbt spent on the node |
| `rows_affected` | The row count the database reported for the node's statement |
| `failures` | Failing rows of a test; null for other nodes |
| `message` | dbt's message for the node |
| `recorded_at` | When the row was written |

One nightly run is three invocations with the same `airflow_run_id`. Rows
older than 180 days (`node_results_retention_days`) are deleted by the next
audited invocation. Airflow's task state remains the record of whether a run
succeeded: a command that fails before dbt finishes writes no row.

A nightly run that succeeds copies `manifest.json` and the `run_results.json`
of its last command to `/opt/airflow/data/gridskew/artifacts/<UTC day>/` and
to `artifacts/latest/` in the Airflow container. Each file is replaced whole.
Day folders older than 30 days are removed. A failed run publishes nothing.

## Balancing instructions and outage notices

`stg_elexon__boalf` and `stg_elexon__remit` are source-grain views.
`int_elexon__boa_by_period`, in the private `analysis` group, splits each
instructed ramp into settlement half-hours and integrates it to MWh with the
`ramp_mwh` macro; it reads the latest capture of each ramp point, because an
acceptance that crosses midnight arrives in two daily polls. No model yet
chooses the current row of a REMIT notice. The nightly job runs the data
tests of these views; no scheduled job builds them.

## Shortfall

Two private incremental tables in the `analysis` group answer how far a
unit's metered output is from what it was expected to produce.

`int_elexon__bm_unit_cohort` is the cohort: every registry unit with a
published fuel type, grouped by `elexon_fuel_codes`. A unit without a fuel
type has no physical baseline and is outside every shortfall result.

`int_elexon__instruction_intervals` holds, for each unit and half-hour, the
stretches of time in which one acceptance was in force. Acceptances overlap,
and a later acceptance supersedes an earlier one for the minutes they share
and no others, which is how the balancing mechanism settles them. Each
half-hour is cut wherever an acceptance starts or stops and every piece goes
to the most recently issued acceptance that covers it; the PN applies where
no acceptance does.

`int_shortfall_by_unit_period` has one row per cohort unit and half-hour
with a PN. Expected output is the PN energy outside the instructed stretches
plus the instructed energy inside them; `unexplained_shortfall_mwh` is the
expected energy minus the latest settlement run's metered energy, and
`deviation_from_pn_mwh` splits into `instructed_deviation_mwh` plus that
shortfall. A period that cannot be judged keeps its row and says why in
`determinability`. Both tables use the period tables' incremental rule and
are named in the nightly DAG's `PERIOD_TABLES`. A registry change that
alters a cohort unit's fuel type or Elexon ID reaches its rows the next
night. Two registry events do not, and need a full refresh: a unit that
loses its fuel type keeps its rows (the analyses join the cohort, so it
leaves the results at once), and another unit starting or stopping to
share a cohort unit's Elexon ID, which `warn_cohort_elexon_id_shared`
reports each night.

Three analyses read the table and no job runs them:
`q3_metered_vs_notified.sql` (median and interquartile range of the
deviation per fuel group), `q12_instructed_vs_residual.sql` (the instructed
share of the deviation) and `q3_coverage.sql` (what the cohort holds against
every mapped unit-period). Every result is conditional on the cohort.

## Carbon forecast trajectory

Four views in the private `analysis` group prepare the forecast drift question:
`int_carbon_forecast__revisions`, `int_carbon_forecast__by_period`,
`int_carbon_outturn__latest` and `int_carbon_error_by_period`. The nightly job
runs their key tests, which return a count of failing rows and no forecast
value; no scheduled job builds them.
`analyses/q1_forecast_drift_population.sql` counts the population without
reading a forecast value and can run on any day.
`analyses/q1_forecast_drift_verdict.sql` is read once, when the population
reaches 2,880 half-hours. See
[Carbon forecast trajectory](../docs/carbon-forecast-trajectory.md).

## Local setup

Install the pinned development dependencies from the repository root:

```powershell
pip install -r requirements-dev.txt
```

This installs dbt, the PostgreSQL adapter, pytest and the ingestion runtime.

Make these environment variables available before running dbt:

- `DBT_PROFILES_DIR`: absolute path to the repository's `dbt_profiles` folder
- `DBT_HOST`: PostgreSQL host
- `DBT_PORT`: production PostgreSQL port, normally `5435`
- `DBT_USER`: restricted dbt role
- `DBT_PASSWORD`: password for that role

Run dbt commands from the repository's `dbt` folder.

Commands that change nothing in the database:

```powershell
dbt debug
dbt source freshness
dbt test --select "source:*"
dbt test --select "test_type:unit"
```

Seed loads on a verified disposable database only; live seed changes follow
the release runbook:

```powershell
dbt seed --select elexon_settlement_run_codes elexon_fuel_codes carbon_intensity_bands
dbt test --select elexon_settlement_run_codes elexon_fuel_codes carbon_intensity_bands
```

Use `dbt seed --full-refresh` after changing a seed's columns or configured
types. Ordinary value changes need only a normal `dbt seed` run.

Raw read-only permissions protect raw records, but do not protect downstream
views or snapshots from replacement. Live model changes and full refreshes
follow the observed platform release workflow with prechecks, explicit
selectors, descendant recovery and the shared dbt pool.

Freshness runs hourly. The BM-unit job loads `elexon_fuel_codes`, tests the
registry and records its snapshot. The nightly job updates the two private
period tables and runs the project's data tests without recreating views. CI
runs a full build against an ephemeral PostgreSQL service, then the nightly
commands; it does not deploy relations to the homelab.

On a pull request CI also runs
`dbt build --select +state:modified+ --state <manifest of the last main run>`.
It rebuilds the changed nodes with everything above and below them. The
manifest is a workflow artifact that each `main` run uploads; when none
exists the step is skipped and the full build stands alone.

`elexon_settlement_run_codes` is consumed by the B1610 period model and
`elexon_fuel_codes` by registry validation. `carbon_intensity_bands` has no
current model consumer; do not infer a production load from its presence in
the repository.
