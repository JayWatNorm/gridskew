CREATE FUNCTION raw.lock_bm_unit_registry() RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = raw, pg_temp AS $$
BEGIN
    LOCK TABLE raw.elexon_bm_units_extracts, raw.elexon_bm_units IN SHARE MODE;
END $$;

ALTER FUNCTION raw.lock_bm_unit_registry() OWNER TO gridskew;

REVOKE EXECUTE ON FUNCTION raw.lock_bm_unit_registry() FROM PUBLIC;

COMMENT ON FUNCTION raw.lock_bm_unit_registry() IS
  'Takes the SHARE lock the BM-unit capture holds while dbt reads the registry, for a role without UPDATE rights. Held until the caller''s transaction ends.';
