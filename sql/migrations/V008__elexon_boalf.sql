CREATE TABLE raw.elexon_boalf (
    national_grid_bm_unit    text        NOT NULL,
    bm_unit                  text,
    acceptance_number        integer     NOT NULL,
    acceptance_time          timestamptz NOT NULL,
    settlement_date          date        NOT NULL,
    settlement_period_from   smallint    NOT NULL,
    settlement_period_to     smallint    NOT NULL,
    time_from                timestamptz NOT NULL,
    time_to                  timestamptz NOT NULL,
    level_from               integer     NOT NULL,
    level_to                 integer     NOT NULL,
    so_flag                  boolean,
    deemed_bo_flag           boolean,
    stor_flag                boolean,
    rr_flag                  boolean,
    amendment_flag           text,
    retrieved_at             timestamptz NOT NULL,
    PRIMARY KEY (national_grid_bm_unit, acceptance_number, time_from, retrieved_at)
);

COMMENT ON TABLE raw.elexon_boalf IS
  'Bid-offer acceptance levels: the MW level a unit was instructed to follow,
 as ramp points. One row per unit, acceptance, ramp point and poll.';

COMMENT ON COLUMN raw.elexon_boalf.acceptance_number IS
  'Not unique on its own: two units can hold the same number.';

COMMENT ON COLUMN raw.elexon_boalf.settlement_date IS
  'British local time, for the period the ramp point starts in. Never derive
 this from time_from.';

COMMENT ON COLUMN raw.elexon_boalf.level_from IS
  'MW at time_from: an absolute level, not a change from the notified level.
 A straight line to level_to. Negative means importing.';

COMMENT ON COLUMN raw.elexon_boalf.level_to IS
  'MW at time_to. See level_from.';

COMMENT ON COLUMN raw.elexon_boalf.so_flag IS
  'Believed to mark system (not energy) actions. The API does not document it.';

COMMENT ON COLUMN raw.elexon_boalf.retrieved_at IS
  'When this poll ran. Part of the key: BOALF rows carry no revision number.';

CREATE INDEX idx_raw_elexon_boalf_retrieved_at ON raw.elexon_boalf (retrieved_at);
