CREATE SCHEMA IF NOT EXISTS raw;

CREATE TABLE IF NOT EXISTS raw.elexon_pn (
    bm_unit                 text NOT NULL,
    national_grid_bm_unit   text,   
    settlement_date         date NOT NULL,
    settlement_period       smallint NOT NULL,
    time_from               TIMESTAMPTZ NOT NULL,
    time_to                 TIMESTAMPTZ NOT NULL,
    level_from              int NOT NULL,
    level_to                int NOT NULL,
    retrieved_at            TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (bm_unit, time_from, retrieved_at)
);
COMMENT ON TABLE raw.elexon_pn IS
  'What each unit said it would generate. One row per unit per declared segment,
 per poll, so a unit can have several rows in one settlement period. PN must be 
 checked against QPN for the cost of internal processes';

COMMENT ON COLUMN raw.elexon_pn.settlement_date IS
  'British local time. Never derive this from time_from.';

COMMENT ON COLUMN raw.elexon_pn.settlement_period IS
  '1 to 50. Normally 48 per day; 46 and 50 on clock-change days.';

COMMENT ON COLUMN raw.elexon_pn.level_from IS
  'MW at time_from. A straight line to level_to, not an average for the period.
 Negative means importing.';

COMMENT ON COLUMN raw.elexon_pn.level_to IS
  'MW at time_to. See level_from.';

COMMENT ON COLUMN raw.elexon_pn.retrieved_at IS
  'When this poll ran. PN has no revision marker, so this is the only way to spot
 a resubmitted notification.';

COMMENT ON COLUMN raw.elexon_pn.national_grid_bm_unit IS
  'National Grid identifier for the same unit. Needed to join REMIT outage notices.';


-- CREATE INDEX IF NOT EXISTS idx_raw_elexon_pn_retrieved_at ON raw.elexon_pn (retrieved_at);