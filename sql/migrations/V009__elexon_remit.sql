CREATE TABLE raw.elexon_remit (
    mrid                   text        NOT NULL,
    revision_number        integer     NOT NULL,
    publish_time           timestamptz NOT NULL,
    created_time           timestamptz NOT NULL,
    message_type           text,
    event_type             text,
    unavailability_type    text,
    event_status           text,
    asset_id               text,
    affected_unit          text,
    fuel_type              text,
    normal_capacity        numeric,
    available_capacity     numeric,
    unavailable_capacity   numeric,
    event_start_time       timestamptz NOT NULL,
    event_end_time         timestamptz,
    payload                jsonb       NOT NULL,
    retrieved_at           timestamptz NOT NULL,
    PRIMARY KEY (mrid, revision_number, publish_time, created_time)
);

COMMENT ON TABLE raw.elexon_remit IS
  'REMIT messages: outage and availability notices that market participants
 publish. One row per publication of a message.';

COMMENT ON COLUMN raw.elexon_remit.mrid IS
  'Message identifier, shared by every revision of one notice.';

COMMENT ON COLUMN raw.elexon_remit.revision_number IS
  'Not unique within an mrid: one number can be published more than once
 with different content, so both times are part of the key.';

COMMENT ON COLUMN raw.elexon_remit.publish_time IS
  'When Elexon published this row. The API filters on it.';

COMMENT ON COLUMN raw.elexon_remit.unavailability_type IS
  'Planned or Unplanned observed; the API does not list the values. Absent
 on messages of type OtherMarketInformation.';

COMMENT ON COLUMN raw.elexon_remit.asset_id IS
  'Usually an Elexon BM-unit identifier such as T_DRAXX-1, but free text.';

COMMENT ON COLUMN raw.elexon_remit.normal_capacity IS
  'MW when fully available, as published. Can be fractional or negative.';

COMMENT ON COLUMN raw.elexon_remit.payload IS
  'The complete source row, including the fields with no column here, such
 as outageProfile.';

COMMENT ON COLUMN raw.elexon_remit.retrieved_at IS
  'When the poll that first stored this row ran. Not part of the key: a
 repeated read of the same publication inserts nothing.';

CREATE INDEX idx_raw_elexon_remit_publish_time ON raw.elexon_remit (publish_time);
