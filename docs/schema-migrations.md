# Schema migrations

The `raw` schema is built from versioned SQL files that
[Flyway](https://documentation.red-gate.com/flyway) applies in order and
records, with a checksum, in `raw.flyway_schema_history`.

## Layout

| Path | Holds | Applied by |
|---|---|---|
| `sql/migrations/` | Versioned migrations for the `raw` schema | Flyway |
| `sql/provision/` | psql scripts with `-v` variables: roles, grants, and schemas outside `raw` | an administrator, per environment |

## Rules

- A file is named `V<NNN>__<snake_case_description>.sql`: a three-digit
  version, then two underscores.
- **A migration that has been applied anywhere is never edited.** Flyway
  compares checksums and stops when one differs. A schema change is a new
  version.
- A new migration uses plain `CREATE TABLE`, without `IF NOT EXISTS`, so an
  object that already exists stops the migration. `V001` to `V007` keep
  `IF NOT EXISTS`: those tables existed before Flyway, and each database that
  holds them is baselined at version 7.
- Expand, then contract. One release adds a table or column. A later release
  drops or renames the old shape, after no code reads it.
- Grants and roles differ between environments, so they stay in
  `sql/provision/` and out of migrations.

## Where migrations run

**CI** applies every migration to an empty PostgreSQL before fixtures load, in
both jobs, with the `flyway/flyway` image at the version pinned in
`.github/workflows/ci.yml`.

**A workstation without Docker** applies the same files in order with `psql`:

```powershell
Get-ChildItem sql\migrations\V*.sql | Sort-Object Name | ForEach-Object {
    psql -h localhost -U postgres -d gridskew_dev -v ON_ERROR_STOP=1 -f $_.FullName
}
```

This creates the tables without a history table, which is enough for a
disposable database.

**Production** receives a migration as its own step, before the release of
the code that needs it. The steps are in the platform runbook,
`homelab-platform/docs/gridskew-release.md`.
