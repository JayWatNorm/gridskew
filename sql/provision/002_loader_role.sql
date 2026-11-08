-- Run as an administrator with psql -v loader_role=<the loader role>.
-- Creates the role the ingestion connection uses, without login: the login
-- and password are a separate host step. A role of that name that already
-- exists stops the script, so nothing is inherited from a reused role; the
-- grants are in 004_loader_grants.sql.
\set ON_ERROR_STOP on

BEGIN;

SELECT format(
    $guard$
    DO $body$
    BEGIN
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = %L) THEN
            RAISE EXCEPTION 'role %% already exists; the loader role must be created fresh',
                %L;
        END IF;
    END $body$
    $guard$,
    :'loader_role',
    :'loader_role'
) \gexec

SELECT format(
    'CREATE ROLE %I NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS',
    :'loader_role'
) \gexec

COMMIT;
