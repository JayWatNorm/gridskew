# gridskew dbt project

This project transforms the five production `raw` tables into the Silver and
Gold layers. dbt reads `gridskew_prod.raw` through the restricted
`gridskew_dbt` role and writes development objects to `dbt_dev`.

## Project structure

| Path | Purpose |
|---|---|
| `models/staging/` | One-to-one source models with explicit columns and standard names and types |
| `models/intermediate/` | Joins, grain changes and reusable business logic |
| `models/marts/` | Facts, dimensions and final aggregates |
| `../dbt_profiles/` | Local profile; credentials come from environment variables |

Models are materialised as views unless a model defines a different strategy.
Staging models must not join, aggregate or deduplicate source rows.

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

Build and test the selected models together:

```powershell
dbt build
```

The local profile reads production raw data deliberately. Safety comes from
database permissions: `gridskew_dbt` can select from `raw` and write to
`dbt_dev`, but it cannot change `raw` or create schemas.
