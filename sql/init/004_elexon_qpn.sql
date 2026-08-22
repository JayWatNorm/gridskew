CREATE SCHEMA IF NOT EXISTS raw;

CREATE TABLE IF NOT EXISTS raw.elexon_qpn (
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
COMMENT ON TABLE raw.elexon_qpn IS
  'MW used by an internal process inside the unit, netted off the PN. Not output.
 Zero is normal; a missing row means the unit does not submit QPN. Optional and
 not used in Settlement - see docs/sources/elexon/015_qpn.md.';

COMMENT ON COLUMN raw.elexon_qpn.settlement_date IS
  'British local time. Never derive this from time_from.';

COMMENT ON COLUMN raw.elexon_qpn.settlement_period IS
  '1 to 50. Normally 48 per day; 46 and 50 on clock-change days.';

COMMENT ON COLUMN raw.elexon_qpn.level_from IS
  'MW at time_from, not output. A straight line to level_to, not an average for
 the period. Zero is normal.';

COMMENT ON COLUMN raw.elexon_qpn.level_to IS
  'MW at time_to. See level_from.';

COMMENT ON COLUMN raw.elexon_qpn.retrieved_at IS
  'When this poll ran. QPN has no revision marker, so this is the only way to spot
 a resubmitted notification.';

COMMENT ON COLUMN raw.elexon_qpn.national_grid_bm_unit IS
  'National Grid identifier for the same unit. Needed to join REMIT outage notices.';


-- CREATE INDEX IF NOT EXISTS idx_raw_elexon_qpn_retrieved_at ON raw.elexon_qpn (retrieved_at);