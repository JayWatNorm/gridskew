ALTER TABLE raw.endpoint_quarantine
    ADD COLUMN resolved_at timestamptz,
    ADD COLUMN resolved_by text,
    ADD CONSTRAINT endpoint_quarantine_resolution_pair
        CHECK ((resolved_at IS NULL) = (resolved_by IS NULL));

COMMENT ON COLUMN raw.endpoint_quarantine.resolved_at IS
  'UTC time a person marked the row resolved; null while the row still holds its dataset.';

COMMENT ON COLUMN raw.endpoint_quarantine.resolved_by IS
  'Who resolved it and why, in a few words.';
