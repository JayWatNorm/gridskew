CREATE SCHEMA IF NOT EXISTS raw;

CREATE TABLE IF NOT EXISTS raw.endpoint_quarantine (
    quarantine_id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    dataset            text NOT NULL,
    retrieved_at       timestamptz NOT NULL,
    request_context    jsonb NOT NULL,
    validation_errors  jsonb NOT NULL,
    observed_fields    jsonb NOT NULL,
    payload            jsonb NOT NULL,
    quarantined_at     timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON TABLE raw.endpoint_quarantine IS
  'Stores rejected API source rows and their validation findings before typed loading.';

COMMENT ON COLUMN raw.endpoint_quarantine.quarantine_id IS
  'Database-generated identifier for the quarantined source row.';

COMMENT ON COLUMN raw.endpoint_quarantine.dataset IS
  'Dataset and poller associated with the rejected source row.';

COMMENT ON COLUMN raw.endpoint_quarantine.retrieved_at IS
  'UTC time when the poller retrieved the API response.';

COMMENT ON COLUMN raw.endpoint_quarantine.request_context IS
  'Request parameters used for the API call, such as the requested date window.';

COMMENT ON COLUMN raw.endpoint_quarantine.validation_errors IS
  'Structured endpoint contract validation failures.';

COMMENT ON COLUMN raw.endpoint_quarantine.observed_fields IS
  'Field names observed in the rejected source row for rapid shape inspection.';

COMMENT ON COLUMN raw.endpoint_quarantine.payload IS
  'Complete rejected source row stored as JSONB before typed parsing.';

COMMENT ON COLUMN raw.endpoint_quarantine.quarantined_at IS
  'Database time when the rejected source row was written to quarantine.';
