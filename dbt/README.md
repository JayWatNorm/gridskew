# gridskew dbt project

This project transforms the five production `raw` tables. The Silver staging
models are in place; intermediate models and the Gold layer follow in later
build stages. dbt reads `gridskew_prod.raw` through the restricted
`gridskew_dbt` role and writes development objects to `dbt_dev`.

## Project structure

| Path | Purpose |
|---|---|
| `models/staging/` | Source-grain models with explicit columns, standard names and row-level normalisation |
| `models/intermediate/` | Joins, grain changes and reusable business logic |
| `models/marts/` | Facts, dimensions and final aggregates |
| `macros/` | Reusable SQL expressions, including settlement-period conversion |
| `models/staging/*/_*__unit_tests.yml` | Inline mock inputs and expected results for dbt unit tests |
| `../dbt_profiles/` | Local profile; credentials come from environment variables |

Models are materialised as views unless a model defines a different strategy.
Staging models must not join, aggregate or deduplicate source rows.

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

Build and test the selected models together:

```powershell
dbt build
```

The local profile reads production raw data deliberately. Safety comes from
database permissions: `gridskew_dbt` can select from `raw` and write to
`dbt_dev`, but it cannot change `raw` or create schemas.
