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
  'Metered output, MWh per settlement period. One row per unit, period and
 settlement run. Elexon documents a five day lag; measured seven, and around
 twelve over bank holidays because the rule counts working days.';

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
  'When this settlement run was FIRST SEEN, not when the last poll ran. The
 column is named the same as on PN and QPN but means something different:
 retrieved_at is not part of this key, so a re-poll that finds no new run
 inserts nothing and leaves this untouched. That makes re-polls idempotent,
 which matters at this volume. It also makes the column unsuitable for dbt
 source freshness - a healthy daily poll would look stale.';

COMMENT ON COLUMN raw.elexon_b1610.national_grid_bm_unit_id IS
  'Nullable, and null on 71.8% of rows - the exact inverse of PN, where bm_unit
 is the nullable one. That is why this table keys on bm_unit and PN keys on the
 National Grid identifier. Generalising either key to the other table fails on
 the first load. Also note the Id suffix, which PN and QPN do not have: a parse
 copied between the modules raises KeyError rather than producing nulls.';


-- CREATE INDEX IF NOT EXISTS idx_raw_elexon_b1610_retrieved_at ON raw.elexon_b1610 (retrieved_at);