-- Run with psql -v snapshot_schema=<verified target schema> -v dbt_role=<verified role>.
-- S4 uses dbt_dev_snapshots locally and on the homelab. Confirm the target
-- database, dbt role and existing schema owners before this administrator step.
\set ON_ERROR_STOP on

BEGIN;

SELECT format('CREATE SCHEMA IF NOT EXISTS %I', :'snapshot_schema') \gexec
SELECT format(
    'GRANT USAGE, CREATE ON SCHEMA %I TO %I',
    :'snapshot_schema',
    :'dbt_role'
) \gexec
SELECT format('GRANT USAGE, CREATE ON SCHEMA dbt_dev TO %I', :'dbt_role') \gexec
SELECT format('GRANT USAGE ON SCHEMA raw TO %I', :'dbt_role') \gexec
SELECT format(
    'GRANT SELECT ON TABLE raw.elexon_bm_units_extracts, raw.elexon_bm_units TO %I',
    :'dbt_role'
) \gexec

COMMIT;
