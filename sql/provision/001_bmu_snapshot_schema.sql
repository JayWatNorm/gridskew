-- Run with psql -v snapshot_schema=<verified target schema> -v dbt_role=<verified role>,
-- and again whenever a migration adds a source table that dbt reads: the new
-- table's grant line goes here, and the re-run changes nothing else.
-- The snapshot schema is dbt_dev_snapshots, locally and in production.
-- The production models live in schema dbt_dev for historical reasons.
-- Confirm the target database, the dbt role and the existing schema owners
-- before running this administrator step.
\set ON_ERROR_STOP on

BEGIN;

-- A new database has neither dbt schema yet.
CREATE SCHEMA IF NOT EXISTS dbt_dev;
SELECT format('CREATE SCHEMA IF NOT EXISTS %I', :'snapshot_schema') \gexec
SELECT format(
    'GRANT USAGE, CREATE ON SCHEMA %I TO %I',
    :'snapshot_schema',
    :'dbt_role'
) \gexec
SELECT format('GRANT USAGE, CREATE ON SCHEMA dbt_dev TO %I', :'dbt_role') \gexec
SELECT format('GRANT USAGE ON SCHEMA raw TO %I', :'dbt_role') \gexec
SELECT format(
    'GRANT SELECT ON TABLE
        raw.carbon_intensity_forecast,
        raw.carbon_intensity_outturn,
        raw.elexon_pn,
        raw.elexon_qpn,
        raw.elexon_b1610,
        raw.elexon_bm_units_extracts,
        raw.elexon_bm_units,
        raw.elexon_boalf,
        raw.elexon_remit,
        raw.elexon_qas,
        raw.elexon_ndf,
        raw.elexon_indo,
        raw.elexon_windfor,
        raw.elexon_fuelhh
    TO %I',
    :'dbt_role'
) \gexec

COMMIT;
