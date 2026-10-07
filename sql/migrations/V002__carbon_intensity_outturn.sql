CREATE SCHEMA IF NOT EXISTS raw;

CREATE TABLE IF NOT EXISTS raw.carbon_intensity_outturn (
    period_start            TIMESTAMPTZ NOT NULL,
    period_end              TIMESTAMPTZ NOT NULL,
    retrieved_at            TIMESTAMPTZ NOT NULL,
    actual                  integer,
    forecast_final          integer,
    intensity_index         text,
    PRIMARY KEY (period_start, retrieved_at)
);
COMMENT ON TABLE raw.carbon_intensity_outturn IS
  'One row per settlement period, per poll. Actuals are null until settled and may be revised.';

COMMENT ON COLUMN raw.carbon_intensity_outturn.forecast_final IS
  'The API''s last-revision forecast, made minutes before the period. Not comparable to raw.carbon_intensity_forecast.forecast, which is horizon-resolved.';

CREATE INDEX IF NOT EXISTS idx_raw_carbon_intensity_outturn_retrieved_at ON raw.carbon_intensity_outturn (retrieved_at);
