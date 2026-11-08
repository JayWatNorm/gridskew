-- Run with psql -v loader_role=<the loader role> [-v expect_login=true].
-- Prints one row per check of the role's effective rights, then stops with
-- an error if any row is false, so the exit code says whether the role is
-- fit for the connection switch. expect_login defaults to false; pass true
-- on a host where the login step has been done.
\set ON_ERROR_STOP on

\if :{?expect_login}
\else
\set expect_login false
\endif

CREATE TEMP TABLE loader_role_checks AS
WITH raw_tables AS (
    SELECT relname, 'raw.' || quote_ident(relname) AS table_name
    FROM pg_class
    JOIN pg_namespace ON pg_namespace.oid = pg_class.relnamespace
    WHERE nspname = 'raw' AND relkind IN ('r', 'p', 'v', 'm', 'f')
    ORDER BY relname
),
lock_function AS (
    SELECT pg_proc.oid, proowner, prosecdef, proconfig
    FROM pg_proc
    JOIN pg_namespace ON pg_namespace.oid = pg_proc.pronamespace
    WHERE nspname = 'raw' AND proname = 'lock_bm_unit_registry' AND pronargs = 0
),
checks AS (
    SELECT 'role exists' AS check_name,
           EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'loader_role') AS ok
    UNION ALL
    SELECT 'not a superuser', NOT rolsuper
    FROM pg_roles WHERE rolname = :'loader_role'
    UNION ALL
    SELECT 'cannot create roles', NOT rolcreaterole
    FROM pg_roles WHERE rolname = :'loader_role'
    UNION ALL
    SELECT 'cannot create databases', NOT rolcreatedb
    FROM pg_roles WHERE rolname = :'loader_role'
    UNION ALL
    SELECT 'does not bypass row security', NOT rolbypassrls
    FROM pg_roles WHERE rolname = :'loader_role'
    UNION ALL
    SELECT 'login is ' || :'expect_login', rolcanlogin = :'expect_login'::boolean
    FROM pg_roles WHERE rolname = :'loader_role'
    UNION ALL
    SELECT 'a member of no role',
           NOT EXISTS (
               SELECT 1 FROM pg_auth_members
               JOIN pg_roles ON pg_roles.oid = pg_auth_members.member
               WHERE rolname = :'loader_role'
           )
    UNION ALL
    SELECT 'owns no relation',
           NOT EXISTS (
               SELECT 1 FROM pg_class
               JOIN pg_roles ON pg_roles.oid = pg_class.relowner
               WHERE rolname = :'loader_role'
           )
    UNION ALL
    SELECT 'owns no schema',
           NOT EXISTS (
               SELECT 1 FROM pg_namespace
               JOIN pg_roles ON pg_roles.oid = pg_namespace.nspowner
               WHERE rolname = :'loader_role'
           )
    UNION ALL
    SELECT 'owns no function',
           NOT EXISTS (
               SELECT 1 FROM pg_proc
               JOIN pg_roles ON pg_roles.oid = pg_proc.proowner
               WHERE rolname = :'loader_role'
           )
    UNION ALL
    SELECT 'owns no database',
           NOT EXISTS (
               SELECT 1 FROM pg_database
               JOIN pg_roles ON pg_roles.oid = pg_database.datdba
               WHERE rolname = :'loader_role'
           )
    UNION ALL
    SELECT 'CONNECT on database ' || current_database(),
           has_database_privilege(:'loader_role', current_database(), 'CONNECT')
    UNION ALL
    SELECT 'USAGE on schema raw', has_schema_privilege(:'loader_role', 'raw', 'USAGE')
    UNION ALL
    SELECT 'no CREATE on schema raw',
           NOT has_schema_privilege(:'loader_role', 'raw', 'CREATE')
    UNION ALL
    SELECT 'SELECT on ' || table_name,
           has_table_privilege(:'loader_role', table_name, 'SELECT')
    FROM raw_tables
    UNION ALL
    SELECT 'INSERT on ' || table_name,
           has_table_privilege(:'loader_role', table_name, 'INSERT')
    FROM raw_tables WHERE relname <> 'flyway_schema_history'
    UNION ALL
    SELECT 'no INSERT on ' || table_name,
           NOT has_table_privilege(:'loader_role', table_name, 'INSERT')
    FROM raw_tables WHERE relname = 'flyway_schema_history'
    UNION ALL
    SELECT 'no UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER or column UPDATE on '
           || table_name,
           NOT has_table_privilege(:'loader_role', table_name, 'UPDATE')
           AND NOT has_table_privilege(:'loader_role', table_name, 'DELETE')
           AND NOT has_table_privilege(:'loader_role', table_name, 'TRUNCATE')
           AND NOT has_table_privilege(:'loader_role', table_name, 'REFERENCES')
           AND NOT has_table_privilege(:'loader_role', table_name, 'TRIGGER')
           AND NOT has_any_column_privilege(:'loader_role', table_name, 'UPDATE')
    FROM raw_tables
    UNION ALL
    SELECT 'EXECUTE on raw.lock_bm_unit_registry()',
           has_function_privilege(
               :'loader_role', 'raw.lock_bm_unit_registry()', 'EXECUTE'
           )
    UNION ALL
    SELECT 'raw.lock_bm_unit_registry() exists once',
           (SELECT count(*) FROM lock_function) = 1
    UNION ALL
    SELECT 'raw.lock_bm_unit_registry() is owned by gridskew',
           (SELECT rolname FROM pg_roles WHERE pg_roles.oid = proowner) = 'gridskew'
    FROM lock_function
    UNION ALL
    SELECT 'raw.lock_bm_unit_registry() is SECURITY DEFINER', prosecdef
    FROM lock_function
    UNION ALL
    SELECT 'raw.lock_bm_unit_registry() searches raw, then pg_temp',
           proconfig @> ARRAY['search_path=raw, pg_temp']
    FROM lock_function
)
SELECT row_number() OVER () AS step, check_name, ok FROM checks;

SELECT step, check_name, ok FROM loader_role_checks ORDER BY step;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM loader_role_checks WHERE ok IS DISTINCT FROM true) THEN
        RAISE EXCEPTION 'the loader role failed a check; see the rows above';
    END IF;
END $$;
