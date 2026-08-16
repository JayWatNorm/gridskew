CREATE SCHEMA IF NOT EXISTS raw;

CREATE TABLE IF NOT EXISTS raw.carbon_intensity_forecast (
    period_start            TIMESTAMPTZ NOT NULL,
    period_end              TIMESTAMPTZ NOT NULL,
    retrieved_at            TIMESTAMPTZ NOT NULL,
    forecast                 integer NOT NULL,
    actual                  integer,
    intensity_index         text,
    PRIMARY KEY (period_start, retrieved_at)
);
COMMENT ON TABLE raw.carbon_intensity_forecast IS 'One row per settlement period per poll.';
CREATE INDEX IF NOT EXISTS idx_raw_retrieved_at ON raw.carbon_intensity_forecast (retrieved_at);
