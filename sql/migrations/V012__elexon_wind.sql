CREATE TABLE raw.elexon_windfor (
    start_time    timestamptz NOT NULL,
    publish_time  timestamptz NOT NULL,
    generation    integer     NOT NULL,
    retrieved_at  timestamptz NOT NULL,
    PRIMARY KEY (start_time, publish_time)
);

COMMENT ON TABLE raw.elexon_windfor IS
  'Wind generation forecast (WINDFOR): one row per hourly point and issue.
 Every issue is kept; each forecasts about three days of hourly points. Covers
 only wind farms visible to NESO with operational metering, so embedded wind
 is not in it.';

COMMENT ON COLUMN raw.elexon_windfor.start_time IS
  'The hour the point forecasts, UTC, as the source gives it. Part of the key.';

COMMENT ON COLUMN raw.elexon_windfor.publish_time IS
  'The source publish-time stamp for this issue, stored as given (marked UTC).
 The stamps move by an hour between summer and winter; that summer stamps
 show UK clock time is a working inference. Part of the key; the API filters
 on it.';

COMMENT ON COLUMN raw.elexon_windfor.generation IS
  'MW. Whether a point is an hourly mean or an instant is not documented.';

COMMENT ON COLUMN raw.elexon_windfor.retrieved_at IS
  'When the request that first stored this row ran. Not part of the key:
 fetching a publish window again inserts nothing.';

CREATE TABLE raw.elexon_fuelhh (
    settlement_date    date        NOT NULL,
    settlement_period  smallint    NOT NULL,
    start_time         timestamptz NOT NULL,
    fuel_type          text        NOT NULL,
    publish_time       timestamptz NOT NULL,
    generation         integer     NOT NULL,
    retrieved_at       timestamptz NOT NULL,
    PRIMARY KEY (settlement_date, settlement_period, fuel_type, publish_time)
);

COMMENT ON TABLE raw.elexon_fuelhh IS
  'Half-hourly generation outturn by fuel type (FUELHH): one row per
 settlement period, fuel type and publication. Named for the dataset; only
 fuel type WIND is loaded. Taken to be metered generation (inferred).';

COMMENT ON COLUMN raw.elexon_fuelhh.settlement_date IS
  'British local time. Never derive it from start_time.';

COMMENT ON COLUMN raw.elexon_fuelhh.start_time IS
  'Start of the settlement period, UTC, as the source gives it.';

COMMENT ON COLUMN raw.elexon_fuelhh.fuel_type IS
  'The source fuel type code, such as WIND. Part of the key.';

COMMENT ON COLUMN raw.elexon_fuelhh.publish_time IS
  'When Elexon published this value, UTC. Part of the key, so a later
 publication for the same period is kept beside the first.';

COMMENT ON COLUMN raw.elexon_fuelhh.generation IS
  'MW, averaged over the settlement period.';

COMMENT ON COLUMN raw.elexon_fuelhh.retrieved_at IS
  'When the request that first stored this row ran. Not part of the key:
 fetching a settlement date again inserts nothing.';

CREATE INDEX idx_raw_elexon_windfor_retrieved_at ON raw.elexon_windfor (retrieved_at);
CREATE INDEX idx_raw_elexon_fuelhh_retrieved_at ON raw.elexon_fuelhh (retrieved_at);
