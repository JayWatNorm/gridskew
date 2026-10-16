CREATE TABLE raw.elexon_qas (
    settlement_date                                date        NOT NULL,
    settlement_period                              integer     NOT NULL,
    national_grid_bm_unit                          text        NOT NULL,
    bm_unit                                        text,
    bm_unit_applicable_balancing_services_volume   numeric     NOT NULL,
    retrieved_at                                   timestamptz NOT NULL,
    PRIMARY KEY (settlement_date, national_grid_bm_unit, settlement_period, retrieved_at)
);

COMMENT ON TABLE raw.elexon_qas IS
  'Balancing services volume (QAS, the ABSVD that NESO sends for each BM unit
 and settlement period). One row per unit, period and capture; each capture is
 one settlement date in one request. The key leads with the date because every
 read is by date.';

COMMENT ON COLUMN raw.elexon_qas.settlement_date IS
  'British local time. The poller asks by settlement date, never by time.';

COMMENT ON COLUMN raw.elexon_qas.bm_unit IS
  'Elexon identifier. Secondary units (2__, V__) differ from the National Grid
 identifier beyond the prefix.';

COMMENT ON COLUMN raw.elexon_qas.bm_unit_applicable_balancing_services_volume IS
  'MWh, positive or negative. Settlement adds it to the expected volume (BSC
 Section T 4.3.2). Normally sent only where non-zero, but exact zeros occur.';

COMMENT ON COLUMN raw.elexon_qas.retrieved_at IS
  'When this capture ran. Part of the key: the source carries no version, so a
 resubmitted value is kept beside the first.';

CREATE INDEX idx_raw_elexon_qas_retrieved_at ON raw.elexon_qas (retrieved_at);
