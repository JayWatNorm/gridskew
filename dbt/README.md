# gridskew dbt project

This project transforms raw API captures and owns small, version-controlled
reference datasets. S4 adds a BM-unit current dimension and an observed-history
snapshot to the existing staging models and S3 seeds. The local profile reads
`gridskew_prod.raw` and writes development models to `dbt_dev`; the homelab
profile has a separate production target and runtime role. Verify the effective
target and user before running commands that create or update relations.

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

Run the current-state and snapshot steps in order against a verified target:

```powershell
dbt seed --select elexon_fuel_codes
dbt build --select +dim_bm_unit
dbt test --select assert_bm_units_staging_grain assert_bm_units_current_extract_count assert_bm_units_duplicate_attributes_agree
dbt snapshot --select snap_elexon__bm_units
```

The single daily Airflow DAG runs these after capturing a complete extract and
rechecks its manifest immediately before snapshotting. Local dbt target schema
`dbt_dev` generates `dbt_dev_snapshots`; the checked-in homelab target schema
`public` generates `public_snapshots`. The snapshot schema needs to exist and
grant `USAGE, CREATE` to the verified dbt runtime role. Use the separate
administrator-run SQL and rollout sequence in
[the deployment guide](../docs/deployment.md). Do not create a fresh snapshot
over existing production history. The S4 code has passed fixture-backed dbt
and lifecycle checks locally; production rollout is pending.

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

Load and test the three reference seeds in the development target:

```powershell
dbt seed --select elexon_settlement_run_codes elexon_fuel_codes carbon_intensity_bands
dbt test --select elexon_settlement_run_codes elexon_fuel_codes carbon_intensity_bands
```

Use `dbt seed --full-refresh` after changing a seed's columns or configured
types. Ordinary value changes need only a normal `dbt seed` run.

Build and test the selected models together:

```powershell
dbt build
```

The local profile reads production raw data deliberately. Safety comes from
database permissions: `gridskew_dbt` can select from `raw` and write to
`dbt_dev`, but it cannot change `raw` or create schemas.

The production Airflow dbt DAG currently runs `dbt source freshness` only. It
does not materialise models or seeds. GitHub CI runs a full build against an
ephemeral PostgreSQL service; that proves the project but does not deploy its
relations to the homelab. A production seed therefore needs an explicit
release-time `dbt seed` or the future scheduled build job when a production
model first depends on it.
