CREATE TABLE raw.elexon_ndf (
    settlement_date    date        NOT NULL,
    settlement_period  smallint    NOT NULL,
    start_time         timestamptz NOT NULL,
    boundary           text        NOT NULL,
    publish_time       timestamptz NOT NULL,
    demand             integer     NOT NULL,
    retrieved_at       timestamptz NOT NULL,
    PRIMARY KEY (settlement_date, settlement_period, boundary, publish_time)
);

COMMENT ON TABLE raw.elexon_ndf IS
  'National Demand forecast (NDF): one row per settlement period, boundary
 and issue. Every issue is kept; each re-forecasts the next day or two.
 Includes transmission losses; excludes interconnectors, station transformers
 and pumped storage demand.';

COMMENT ON COLUMN raw.elexon_ndf.settlement_date IS
  'British local time. Never derive it from start_time.';

COMMENT ON COLUMN raw.elexon_ndf.start_time IS
  'Start of the settlement period, UTC, as the source gives it.';

COMMENT ON COLUMN raw.elexon_ndf.boundary IS
  'N (national) on every observed row.';

COMMENT ON COLUMN raw.elexon_ndf.publish_time IS
  'When Elexon published this issue, UTC. Part of the key; the API filters
 on it.';

COMMENT ON COLUMN raw.elexon_ndf.demand IS
  'MW, averaged over the settlement period.';

COMMENT ON COLUMN raw.elexon_ndf.retrieved_at IS
  'When the request that first stored this row ran. Not part of the key:
 fetching a publish window again inserts nothing.';

CREATE TABLE raw.elexon_indo (
    settlement_date    date        NOT NULL,
    settlement_period  smallint    NOT NULL,
    start_time         timestamptz NOT NULL,
    publish_time       timestamptz NOT NULL,
    demand             integer     NOT NULL,
    retrieved_at       timestamptz NOT NULL,
    PRIMARY KEY (settlement_date, settlement_period, publish_time)
);

COMMENT ON TABLE raw.elexon_indo IS
  'Initial National Demand outturn (INDO): one row per settlement period and
 issue. The same definitional family as NDF; never compare it with TSDF.';

COMMENT ON COLUMN raw.elexon_indo.settlement_date IS
  'British local time. Never derive it from start_time.';

COMMENT ON COLUMN raw.elexon_indo.start_time IS
  'Start of the settlement period, UTC, as the source gives it.';

COMMENT ON COLUMN raw.elexon_indo.publish_time IS
  'When Elexon published this issue, UTC. Part of the key; the API filters
 on it.';

COMMENT ON COLUMN raw.elexon_indo.demand IS
  'MW, averaged over the settlement period.';

COMMENT ON COLUMN raw.elexon_indo.retrieved_at IS
  'When the request that first stored this row ran. Not part of the key:
 fetching a publish window again inserts nothing.';

CREATE INDEX idx_raw_elexon_ndf_retrieved_at ON raw.elexon_ndf (retrieved_at);
CREATE INDEX idx_raw_elexon_indo_retrieved_at ON raw.elexon_indo (retrieved_at);
