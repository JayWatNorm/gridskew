-- Run as an administrator with psql -v loader_role=<the loader role>, after
-- 002_loader_role.sql, and again whenever a migration adds a source table:
-- the new table's grant lines go here, and the re-run changes nothing else.
-- The role must still be as 002 created it (no superuser, create-role,
-- create-database or bypass-RLS flag, and a member of no role); login is
-- allowed either way. A role that differs gets no grant.
\set ON_ERROR_STOP on

BEGIN;

SELECT format(
    $guard$
    DO $body$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_roles
            WHERE rolname = %L
              AND NOT rolsuper
              AND NOT rolcreaterole
              AND NOT rolcreatedb
              AND NOT rolbypassrls
              AND NOT EXISTS (
                  SELECT 1 FROM pg_auth_members WHERE member = pg_roles.oid
              )
        ) THEN
            RAISE EXCEPTION 'role %% is missing, has a privileged flag or is a member of a role; no grant made',
                %L;
        END IF;
    END $body$
    $guard$,
    :'loader_role',
    :'loader_role'
) \gexec

SELECT format(
    'GRANT CONNECT ON DATABASE %I TO %I',
    current_database(),
    :'loader_role'
) \gexec

SELECT format('GRANT USAGE ON SCHEMA raw TO %I', :'loader_role') \gexec

SELECT format(
    'GRANT SELECT, INSERT ON TABLE
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
        raw.elexon_fuelhh,
        raw.endpoint_quarantine
    TO %I',
    :'loader_role'
) \gexec

-- Flyway's history table exists wherever Flyway has run (production and
-- CI); the workstation recipe applies migrations with psql and has none.
SELECT format(
    'GRANT SELECT ON TABLE raw.flyway_schema_history TO %I',
    :'loader_role'
)
WHERE to_regclass('raw.flyway_schema_history') IS NOT NULL \gexec

SELECT format(
    'GRANT USAGE ON SEQUENCE raw.endpoint_quarantine_quarantine_id_seq TO %I',
    :'loader_role'
) \gexec

SELECT format(
    'GRANT EXECUTE ON FUNCTION raw.lock_bm_unit_registry() TO %I',
    :'loader_role'
) \gexec

COMMIT;
