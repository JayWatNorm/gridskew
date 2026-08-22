CREATE SCHEMA IF NOT EXISTS raw;

CREATE TABLE IF NOT EXISTS raw.elexon_b1610 (
    bm_unit                     text NOT NULL,
    national_grid_bm_unit_id    text,   
    psr_type                    text,
    settlement_date             date NOT NULL,
    settlement_period           smallint NOT NULL,
    half_hour_end_time          TIMESTAMPTZ NOT NULL,
    settlement_run_type         text,
    quantity                    numeric NOT NULL,
    retrieved_at                TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (bm_unit, settlement_date, settlement_period, settlement_run_type, retrieved_at)
);
COMMENT ON TABLE raw.elexon_b1610 IS
  'Metered output, MWh per settlement period. One row per unit, period and
 settlement run, per poll. Arrives about five days late and is revised by later runs.';

COMMENT ON COLUMN raw.elexon_b1610.settlement_date IS
  'British local time. Never derive this from half_hour_end_time.';

COMMENT ON COLUMN raw.elexon_b1610.settlement_period IS
  '1 to 50. Normally 48 per day; 46 and 50 on clock-change days.';

COMMENT ON COLUMN raw.elexon_b1610.half_hour_end_time IS
  'Period end. Naive in the source, unlike PN; UTC attached during parsing.';

COMMENT ON COLUMN raw.elexon_b1610.settlement_run_type IS
  'Which settlement run produced this quantity. Ingest all runs; filtering here
 would hide every restatement. Free text, not enumerated.';

COMMENT ON COLUMN raw.elexon_b1610.quantity IS
  'MWh for the whole period, not MW. Numeric because these get summed.
 Negative for units that consume.';

COMMENT ON COLUMN raw.elexon_b1610.psr_type IS
  'Resource type, for example Generation. Not enumerated in the API.';

COMMENT ON COLUMN raw.elexon_b1610.retrieved_at IS
  'When this poll ran. B1610 has its own revision axis in settlement_run_type.';

COMMENT ON COLUMN raw.elexon_b1610.national_grid_bm_unit_id IS
  'Named with an Id suffix here but not on PN or QPN. A parse copied between the
 modules will produce nulls.';


-- CREATE INDEX IF NOT EXISTS idx_raw_elexon_b1610_retrieved_at ON raw.elexon_b1610 (retrieved_at);