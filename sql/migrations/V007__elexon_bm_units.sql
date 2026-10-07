CREATE SCHEMA IF NOT EXISTS raw;

CREATE TABLE IF NOT EXISTS raw.elexon_bm_units_extracts (
    extract_id uuid PRIMARY KEY,
    retrieved_at timestamptz NOT NULL,
    row_count integer NOT NULL CHECK (row_count > 0),
    unit_count integer NOT NULL CHECK (unit_count > 0),
    CHECK (unit_count <= row_count)
);

COMMENT ON TABLE raw.elexon_bm_units_extracts IS
  'One manifest per committed, complete BM-unit registry response. The latest manifest selects current source state.';

CREATE INDEX IF NOT EXISTS idx_elexon_bm_units_extracts_latest
    ON raw.elexon_bm_units_extracts (retrieved_at DESC, extract_id DESC);

CREATE TABLE IF NOT EXISTS raw.elexon_bm_units (
    extract_id uuid NOT NULL REFERENCES raw.elexon_bm_units_extracts (extract_id),
    source_index integer NOT NULL CHECK (source_index >= 0),
    national_grid_bm_unit text NOT NULL CHECK (btrim(national_grid_bm_unit) <> ''),
    elexon_bm_unit text,
    eic text,
    fuel_type text,
    lead_party_name text,
    bm_unit_type text,
    fpn_flag boolean,
    bm_unit_name text,
    lead_party_id text,
    demand_capacity text,
    generation_capacity text,
    production_or_consumption_flag text,
    transmission_loss_factor text,
    working_day_credit_assessment_import_capability text,
    non_working_day_credit_assessment_import_capability text,
    working_day_credit_assessment_export_capability text,
    non_working_day_credit_assessment_export_capability text,
    credit_qualifying_status boolean NOT NULL,
    demand_in_production_flag boolean NOT NULL,
    gsp_group_id text,
    gsp_group_name text,
    interconnector_id text,
    PRIMARY KEY (extract_id, source_index)
);

COMMENT ON TABLE raw.elexon_bm_units IS
  'Append-only source rows from complete registry responses. A BM unit can appear more than once with different EICs; source_index preserves every row.';

CREATE INDEX IF NOT EXISTS idx_elexon_bm_units_extract_unit
    ON raw.elexon_bm_units (extract_id, national_grid_bm_unit);
