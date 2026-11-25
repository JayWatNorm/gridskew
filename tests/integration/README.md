# Integration checks

CI runs these scripts against its throwaway PostgreSQL database
(`.github/workflows/ci.yml`), after the Flyway migrations, the fixture load
and the dbt build. None calls a live API: every row comes from the captured
fixtures under `tests/fixtures/`.

pytest skips this directory (`norecursedirs` in `pyproject.toml`), and
nothing in here is named `test_*.py`: a run is a plain script, from the
repository root with the virtualenv active, for example

```bash
python -m tests.integration.check_insert_rows
```

They import from `ingestion/` and `tests/`, so they need the repository root
on the path.

## What each one checks

| Script | What it checks | Needs |
|---|---|---|
| `load_ci_fixtures.py` | Loads the captured fixtures through the real routing and loaders; the raw tables come from `sql/migrations` first | Disposable PostgreSQL |
| `check_bmu_snapshot_lifecycle.py` | Real dbt snapshot transitions for the BM-unit registry | Disposable PostgreSQL |
| `check_outturn_gap_refetch.py` | The outturn history gap query, inside one transaction that is rolled back | Disposable PostgreSQL |
| `check_period_tables.py` | The period tables: an incremental run equals a full refresh; runs in its own job on an empty database | Disposable PostgreSQL |
| `check_analyses_compile.py` | Every analysis compiles and runs with `limit 0` on the CI fixtures | Disposable PostgreSQL |
| `check_one_look_analyses.py` | The Q1, Q10, Q25, Q8, Q28, Q29 and Q30 analyses on hand-worked rows, with the connection's time zone set to UTC and to Europe/London: every listed cell, each gate, Q25's acceptance across the London midnight, Q8's gap either side of 0.1 and -0.1, Q28's ratio either side of 1.25, and how Q28 assigns equal-error half-hours when a quintile boundary splits a tie | Disposable PostgreSQL |
| `check_scheduled_set.py` | The nightly DAG's and the BM-unit DAG's default dbt commands, the rules that stop a model or seed replacing a relation, and that the DAG's missing-table guard names every incremental model | dbt's manifest and the nightly DAG |
| `check_nightly_run.py` | The nightly DAG's default dbt commands, run for real after the full build: each succeeds, each node gets one audit row, no view is recreated, and audit rows past retention are deleted | Disposable PostgreSQL |
| `check_database_writes.py` | What the loaders and the quarantine write, read back by column name; that a repeated load is ignored; the B1610 coverage and REMIT quarantine queries; that the demand back-fill holds a UTC publish day with the connection's time zone set to Europe/London; the wind capture's completeness and quarantine queries with the connection's time zone set to UTC, Europe/London, `Etc/GMT-14` and `Etc/GMT+12`; that `warn_wind_capture_days_short` names exactly the days the capture's rule names; two capture runs across a failed request; the quarantine resolution steps on the real table (a REMIT row stops counting once its message is stored, an unresolved row fails the hourly run until it is marked resolved by id, a quarantined WINDFOR day is held until its row is marked resolved and is then loaded, and a row with one resolution column set is refused); the loader role's effective rights (the verification script passes; as the loader, inserts and `ON CONFLICT DO NOTHING` succeed, every delete, update, truncate and direct lock is refused with `42501`, and `raw.lock_bm_unit_registry()` holds a `ShareLock` on both registry tables until rollback; a role that exists with an update right makes `002` fail; a stray table, column or PUBLIC grant makes `003` fail; a `004` re-run changes nothing and stops before any grant on a role with a wrong flag; the owner keeps the resolution and payload updates); and, first, the REMIT re-read (`remit_reread_checks.py`): that it never moves the hourly watermark across an outage and restart, two runs across a failure, a malformed time that fails its day without a quarantine row, and `warn_remit_stored_late` on fixed evaluation days in four connection time zones. Runs after every step that reads the fixtures and deletes its own rows | Disposable PostgreSQL, dbt, psql and the provisioned loader role |
| `check_insert_rows.py` | The shared insert helper: every row lands, each value type binds to its column, a repeated load is ignored and a failed row leaves nothing once rolled back | Disposable PostgreSQL |
| `check_dag_import.py` | Every DAG file imports with the real Airflow: the folder is not empty, no import error, Airflow parses every file, exactly one `dag_id` per file, and Airflow loads each one. Runs in its own CI job inside the `apache/airflow:3.3.2-python3.12` image the platform builds on, with the repository mounted at the project path the DAGs put on `sys.path`; no database | Docker, or an environment with Airflow 3.3.2 installed |

## Building the CI database locally

CI builds its database on Python 3.12 and PostgreSQL 16 (the `postgres:16.15`
image, started with user `postgres`, password `password` and an empty
database `gridskew_dev`). The steps below are those of
`.github/workflows/ci.yml`, in its order, from the repository root:

```bash
export DBT_HOST=localhost DBT_PORT=5432 DBT_USER=postgres DBT_PASSWORD=password DBT_DBNAME=gridskew_dev
export PGPASSWORD=password

python -m pip install pip==26.2.1
pip install -r requirements-dev.txt

psql -h localhost -U postgres -d gridskew_dev -v ON_ERROR_STOP=1 \
  -c "CREATE ROLE gridskew LOGIN PASSWORD 'password'; GRANT CREATE ON DATABASE gridskew_dev TO gridskew;"

docker run --rm --network host \
  -v "$PWD/sql/migrations:/flyway/sql:ro" \
  flyway/flyway:13.8.0 \
  -url=jdbc:postgresql://localhost:5432/gridskew_dev \
  -user=gridskew -password=password \
  -schemas=raw -locations=filesystem:/flyway/sql \
  migrate

PYTHONPATH=. GRIDSKEW_DISPOSABLE_TEST=1 DBT_USER=gridskew python -m tests.integration.load_ci_fixtures

export DBT_PROFILES_DIR=dbt_profiles
dbt parse --project-dir dbt
dbt build --project-dir dbt
```

Before these steps, PostgreSQL 16 runs on port 5432 with an empty local
database `gridskew_dev` and a `postgres` administrator. The Flyway command
assumes Linux host networking; where the container cannot reach the host's
database, or without Docker,
[schema migrations](../../docs/schema-migrations.md) has a `psql` recipe for
the migration step.

The checks then run in the order `.github/workflows/ci.yml` sets, which is
not the table's. Two run elsewhere: `check_period_tables.py` in its own job,
on a database with the migrations and no fixtures, and `check_dag_import.py`
in the Airflow image. Provision the loader role (below) before
`check_database_writes.py`.

This builds the fixture database only. Rerunning a published result needs
the project's own archive of captured data, which is not public.

## Running one locally

The database checks read the CI database variables (`DBT_HOST`, `DBT_PORT`,
`DBT_USER`, `DBT_PASSWORD`, `DBT_DBNAME`; the dbt steps also need
`DBT_PROFILES_DIR`), and refuse to run unless `GRIDSKEW_DISPOSABLE_TEST=1` and
the database is a local `gridskew_dev`. They connect as `postgres`, the
administrator; the migrations and the fixture load run as `gridskew`, so the
`raw` tables and the lock function share that owner
([schema migrations](../../docs/schema-migrations.md)). Before
`check_database_writes.py`, the loader role it exercises is provisioned
as CI does:

```powershell
foreach ($script in "002_loader_role", "004_loader_grants", "003_verify_loader_role") {
    psql -h localhost -U postgres -d gridskew_dev -v ON_ERROR_STOP=1 -v loader_role=gridskew_loader_test -v expect_login=false -f sql\provision\$script.sql
    if ($LASTEXITCODE) { throw "$script failed" }
}
```
