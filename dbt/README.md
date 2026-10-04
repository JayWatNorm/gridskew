# gridskew dbt project

This project transforms raw API captures and owns small, version-controlled
reference datasets. S4 adds a BM-unit current dimension and an observed-history
snapshot to the existing staging models and S3 seeds. S6 adds incremental
period tables for metered (B1610) and committed (PN) energy, exposed as the
`fct_generation` and `fct_commitments` views. The local profile reads
`gridskew_prod.raw` and writes to `dbt_dev`. The deployed S4 and S6 jobs also
select the homelab `dev` target in that production database; these schemas hold
live derived data and observed history. Freshness uses the separate `prod`
target. Verify the effective database, target, schema and user before running
commands that create or update relations.

## Project structure

| Path | Purpose |
|---|---|
| `models/staging/` | Source-grain models with explicit columns, standard names and row-level normalisation |
| `models/intermediate/` | Joins, grain changes and reusable business logic |
| `models/marts/` | Facts, dimensions and final aggregates |
| `snapshots/` | Observed BM-unit attribute history from the first successful capture |
| `macros/` | Reusable SQL expressions, including settlement-period conversion |
| `seeds/` | Small reference datasets, their explicit types, documentation and data tests |
| `models/staging/*/_*__unit_tests.yml` | Inline mock inputs and expected results for dbt unit tests |
| `../dbt_profiles/` | Local profile; credentials come from environment variables |

Models are materialised as views unless a model defines a different strategy.
Staging models must not join, aggregate or deduplicate source rows.

## BM-unit registry lineage

The S4 poller commits each complete response to
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

The S4 change from build to test was released on 26 September 2026; the first
scheduled run after that change has not yet been confirmed. Use the guarded
Airflow task for live retries. Do not replace its tests with
`dbt build --select +dim_bm_unit` as a scheduled or standalone repair.

The deployed S4 job selects the `dev` target in the production database:
`dbt_dev` holds models and `dbt_dev_snapshots` holds observed registry history.
A schema name containing `dev` does not make it disposable. Schema creation,
grants and structural changes follow the administrator-run SQL and observed
release sequence in `homelab-platform/docs/gridskew-release.md` in the platform
checkout. The [homelab CD overview](../docs/deployment.md) explains that boundary.
Do not create a fresh snapshot over existing production history.

That model default does not apply to seeds. `dbt seed` loads each CSV as a
physical table in the target schema. The three S3 seeds are:

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

The two period tables recompute only settlement periods with new captures.
S6 was released on 26 September 2026 and an observed manual nightly run passed;
the first scheduled nightly run has not yet been confirmed.

The following is the normal nightly command sequence for reference. Live runs
use the guarded Airflow task in the shared `gridskew_dbt` pool; these commands
alone do not supply its missing-table guard, connection checks or serialisation:

```powershell
dbt test --select "stg_elexon__b1610,test_type:generic"
dbt run --select int_elexon__b1610_period int_elexon__pn_period_mwh
dbt test --select int_elexon__b1610_period+ int_elexon__pn_period_mwh+ --exclude tag:full_population
```

A manual load outside the capture-time margin, a raw edit, a settlement-run
seed change, a model-logic change or the monthly recovery requires an observed
full refresh. Trigger `gridskew__dbt_nightly` with `full_refresh=true` through
the platform runbook. Its source/run-code checks block before the write, and
its descendant build restores the fact views. Coordinate the shared dbt pool,
verify all descendants and tests afterwards, and follow the runbook's recovery
sequence if a cascade fails. A standalone full-refresh command omits those
controls.

See [model decisions](../docs/decisions.md) and
`homelab-platform/docs/gridskew-release.md` in the platform checkout.

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

Check the profile and database connection:

```powershell
dbt debug
```

This verifies the project, profile, environment variables and PostgreSQL
connection without building models.

Check whether each raw source has loaded within its expected interval:

```powershell
dbt source freshness
```

Run tests attached to all declared sources:

```powershell
dbt test --select "source:*"
```

Run only the fixture-backed unit tests:

```powershell
dbt test --select "test_type:unit"
```

The following seed-load example is for a verified disposable database.
The default local `dev` target is in the production database, so it is not
that disposable context. Live seed changes follow the release runbook.

Load and test the three reference seeds against the verified disposable target:

```powershell
dbt seed --select elexon_settlement_run_codes elexon_fuel_codes carbon_intensity_bands
dbt test --select elexon_settlement_run_codes elexon_fuel_codes carbon_intensity_bands
```

Use `dbt seed --full-refresh` after changing a seed's columns or configured
types. Ordinary value changes need only a normal `dbt seed` run.

Use a full `dbt build` only against a verified disposable database containing
fixtures. The local profile deliberately reads production raw data and writes
to `dbt_dev` in the production database; that schema holds deployed relations.
Raw read-only permissions protect raw records, but do not protect downstream
views or snapshots from replacement. Live model changes and full refreshes
follow the observed platform release workflow with prechecks, explicit
selectors, descendant recovery and the shared dbt pool.

Freshness is configured hourly. S4 loads `elexon_fuel_codes`, tests the registry
and records its snapshot. S6 updates the two private period tables and tests
their descendants without recreating views. First scheduled S4/S6 runs after
the 26 September release remain unconfirmed. CI runs a full build against an
ephemeral PostgreSQL service; it does not deploy relations to the homelab.

`elexon_settlement_run_codes` is consumed by the B1610 period model and
`elexon_fuel_codes` by registry validation. `carbon_intensity_bands` has no
current model consumer; do not infer a production load from its presence in
the repository.
