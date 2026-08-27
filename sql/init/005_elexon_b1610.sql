CREATE SCHEMA IF NOT EXISTS raw;

CREATE TABLE IF NOT EXISTS raw.elexon_b1610 (
    bm_unit                     text NOT NULL,
    national_grid_bm_unit_id    text,   
    psr_type                    text,
    settlement_date             date NOT NULL,
    settlement_period           smallint NOT NULL,
    half_hour_end_time          TIMESTAMPTZ NOT NULL,
    settlement_run_type         text NOT NULL,
    quantity                    numeric NOT NULL,
    retrieved_at                TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (bm_unit, settlement_date, settlement_period, settlement_run_type)
);
COMMENT ON TABLE raw.elexon_b1610 IS
  'Metered export or import energy, MWh per settlement period. One row per unit,
 period and settlement run. Published after five working days, normally seven
 calendar days and sometimes about twelve around bank holidays.';

COMMENT ON COLUMN raw.elexon_b1610.settlement_date IS
  'British local time. Never derive this from half_hour_end_time.';

COMMENT ON COLUMN raw.elexon_b1610.settlement_period IS
  '1 to 50. Normally 48 per day; 46 and 50 on clock-change days.';

COMMENT ON COLUMN raw.elexon_b1610.half_hour_end_time IS
  'Period end. Naive in the source, unlike PN; UTC attached during parsing.';

COMMENT ON COLUMN raw.elexon_b1610.settlement_run_type IS
  'Which settlement run produced this quantity. Not filterable in practice — the
   API serves only the run currently in force, so which run you get is decided by
    the poll offset, not by a parameter.';

COMMENT ON COLUMN raw.elexon_b1610.quantity IS
  'MWh for the whole period, not MW. Numeric because these get summed.
 Negative for units that consume.';

COMMENT ON COLUMN raw.elexon_b1610.psr_type IS
  'Resource type, for example Generation. Not enumerated in the API.';

COMMENT ON COLUMN raw.elexon_b1610.retrieved_at IS
  'When this settlement run was first seen. Unlike PN and QPN, retrieved_at is
 not part of the key because settlement_run_type identifies the revision. A
 re-poll of the same run inserts nothing and leaves this value unchanged.';

COMMENT ON COLUMN raw.elexon_b1610.national_grid_bm_unit_id IS
  'National Grid identifier. Null on 71.8% of rows because many embedded and
 secondary units have no National Grid registration. This table therefore keys
 on bm_unit. The source field has an Id suffix that PN and QPN do not have.';


-- CREATE INDEX IF NOT EXISTS idx_raw_elexon_b1610_retrieved_at ON raw.elexon_b1610 (retrieved_at);
