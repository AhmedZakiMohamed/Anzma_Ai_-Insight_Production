-- Additive Core ingestion foundation. Safe on an existing database: no historical fact rows are updated or deleted.
BEGIN;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version     TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS data_sources (
    source_id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_code        TEXT NOT NULL UNIQUE CHECK (source_code ~ '^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$'),
    building_id        BIGINT NOT NULL REFERENCES buildings(building_id) ON DELETE RESTRICT,
    source_type        TEXT NOT NULL DEFAULT 'API'
                       CHECK (source_type IN ('CONNECTOR','SIMULATOR','MANUAL','API','CORE')),
    connector_name     TEXT NOT NULL,
    connector_version  TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'ACTIVE'
                       CHECK (status IN ('ACTIVE','DEGRADED','ERROR','DISABLED')),
    source_metadata    JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_received_at   TIMESTAMPTZ,
    last_success_at    TIMESTAMPTZ,
    last_error_at      TIMESTAMPTZ,
    last_error         TEXT
);

ALTER TABLE buildings ADD COLUMN IF NOT EXISTS building_code TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS uq_buildings_building_code
    ON buildings(building_code) WHERE building_code IS NOT NULL;

CREATE TABLE IF NOT EXISTS source_credentials (
    credential_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id     BIGINT NOT NULL REFERENCES data_sources(source_id) ON DELETE RESTRICT,
    key_prefix    TEXT NOT NULL UNIQUE,
    key_hash      CHAR(64) NOT NULL CHECK (key_hash ~ '^[0-9a-f]{64}$'),
    scopes        TEXT[] NOT NULL DEFAULT ARRAY['ingest:telemetry','ingest:event','ingest:state']::text[],
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at    TIMESTAMPTZ,
    revoked_at    TIMESTAMPTZ,
    last_used_at  TIMESTAMPTZ,
    CHECK (scopes <@ ARRAY['ingest:telemetry','ingest:event','ingest:state']::text[])
);
CREATE INDEX IF NOT EXISTS idx_source_credentials_source_active
    ON source_credentials(source_id, credential_id) WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS asset_external_ids (
    source_id    BIGINT NOT NULL REFERENCES data_sources(source_id) ON DELETE RESTRICT,
    external_id  TEXT NOT NULL CHECK (length(external_id) BETWEEN 1 AND 128),
    asset_id     BIGINT NOT NULL REFERENCES assets(asset_id) ON DELETE RESTRICT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source_id, external_id)
);
CREATE INDEX IF NOT EXISTS idx_asset_external_ids_asset ON asset_external_ids(asset_id);

CREATE OR REPLACE FUNCTION validate_source_asset_building() RETURNS trigger AS $$
DECLARE source_building BIGINT; asset_building BIGINT;
BEGIN
    SELECT building_id INTO source_building FROM data_sources WHERE source_id = NEW.source_id;
    SELECT l.building_id INTO asset_building
      FROM assets a JOIN locations l USING (location_id) WHERE a.asset_id = NEW.asset_id;
    IF source_building IS NULL OR asset_building IS NULL OR source_building <> asset_building THEN
        RAISE EXCEPTION 'asset mapping must target an asset in the source building'
            USING ERRCODE = '23503';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_asset_external_ids_building') THEN
        CREATE TRIGGER trg_asset_external_ids_building
            BEFORE INSERT OR UPDATE OF source_id, asset_id ON asset_external_ids
            FOR EACH ROW EXECUTE FUNCTION validate_source_asset_building();
    END IF;
END $$;

ALTER TABLE telemetry_records ADD COLUMN IF NOT EXISTS source_id BIGINT
    REFERENCES data_sources(source_id) ON DELETE RESTRICT;
ALTER TABLE telemetry_records ADD COLUMN IF NOT EXISTS received_at TIMESTAMPTZ;
ALTER TABLE telemetry_records ADD COLUMN IF NOT EXISTS quality TEXT
    CHECK (quality IS NULL OR quality IN ('GOOD','UNCERTAIN','BAD'));
ALTER TABLE telemetry_records ADD COLUMN IF NOT EXISTS ingestion_batch_id BIGINT;
ALTER TABLE telemetry_records ADD COLUMN IF NOT EXISTS contract_version TEXT;
ALTER TABLE telemetry_records ADD COLUMN IF NOT EXISTS source_metadata JSONB;

ALTER TABLE event_records ADD COLUMN IF NOT EXISTS source_id BIGINT
    REFERENCES data_sources(source_id) ON DELETE RESTRICT;
ALTER TABLE event_records ADD COLUMN IF NOT EXISTS received_at TIMESTAMPTZ;
ALTER TABLE event_records ADD COLUMN IF NOT EXISTS source_event_id TEXT;
ALTER TABLE event_records ADD COLUMN IF NOT EXISTS ingestion_batch_id BIGINT;
ALTER TABLE event_records ADD COLUMN IF NOT EXISTS contract_version TEXT;
ALTER TABLE event_records ADD COLUMN IF NOT EXISTS source_metadata JSONB;
CREATE UNIQUE INDEX IF NOT EXISTS uq_event_source_event_id
    ON event_records(source_id, source_event_id)
    WHERE source_id IS NOT NULL AND source_event_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS ingestion_batches (
    ingestion_batch_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id           BIGINT NOT NULL REFERENCES data_sources(source_id) ON DELETE RESTRICT,
    batch_id            UUID NOT NULL,
    contract_version    TEXT NOT NULL,
    payload_sha256      CHAR(64) NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    received_at         TIMESTAMPTZ NOT NULL,
    sent_at              TIMESTAMPTZ NOT NULL,
    connector_name      TEXT NOT NULL,
    connector_version   TEXT NOT NULL,
    received_count      INTEGER NOT NULL CHECK (received_count >= 0),
    accepted_count      INTEGER NOT NULL CHECK (accepted_count >= 0),
    duplicate_count     INTEGER NOT NULL CHECK (duplicate_count >= 0),
    rejected_count      INTEGER NOT NULL CHECK (rejected_count >= 0),
    response_payload    JSONB NOT NULL,
    UNIQUE (source_id, batch_id),
    CHECK (accepted_count + duplicate_count + rejected_count = received_count)
);

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'telemetry_records_ingestion_batch_id_fkey') THEN
        ALTER TABLE telemetry_records ADD CONSTRAINT telemetry_records_ingestion_batch_id_fkey
            FOREIGN KEY (ingestion_batch_id) REFERENCES ingestion_batches(ingestion_batch_id) ON DELETE RESTRICT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'event_records_ingestion_batch_id_fkey') THEN
        ALTER TABLE event_records ADD CONSTRAINT event_records_ingestion_batch_id_fkey
            FOREIGN KEY (ingestion_batch_id) REFERENCES ingestion_batches(ingestion_batch_id) ON DELETE RESTRICT;
    END IF;
END $$;
CREATE INDEX IF NOT EXISTS idx_telemetry_source_received ON telemetry_records(source_id, received_at);
CREATE INDEX IF NOT EXISTS idx_event_source_received ON event_records(source_id, received_at);

CREATE TABLE IF NOT EXISTS ingestion_rejections (
    rejection_id       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ingestion_batch_id BIGINT NOT NULL REFERENCES ingestion_batches(ingestion_batch_id) ON DELETE RESTRICT,
    record_index       INTEGER NOT NULL CHECK (record_index >= 0),
    record_type        TEXT,
    reason_code        TEXT NOT NULL,
    message            TEXT NOT NULL,
    payload            JSONB NOT NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (ingestion_batch_id, record_index)
);

CREATE TABLE IF NOT EXISTS equipment_state_records (
    state_id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id         BIGINT NOT NULL REFERENCES data_sources(source_id) ON DELETE RESTRICT,
    asset_id          BIGINT NOT NULL REFERENCES assets(asset_id) ON DELETE RESTRICT,
    observed_at       TIMESTAMPTZ NOT NULL,
    received_at       TIMESTAMPTZ NOT NULL,
    reported_state    TEXT CHECK (reported_state IS NULL OR reported_state IN
                                  ('RUNNING','STOPPED','STANDBY','FAULT','UNKNOWN')),
    availability      TEXT CHECK (availability IS NULL OR availability IN
                                  ('AVAILABLE','UNAVAILABLE','UNKNOWN')),
    reason_code       TEXT,
    message           TEXT,
    source_metadata   JSONB,
    contract_version  TEXT NOT NULL,
    ingestion_batch_id BIGINT NOT NULL REFERENCES ingestion_batches(ingestion_batch_id) ON DELETE RESTRICT,
    UNIQUE (source_id, asset_id, observed_at),
    CHECK (reported_state IS NOT NULL OR availability IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS idx_equipment_state_asset_observed
    ON equipment_state_records(asset_id, observed_at DESC);

CREATE OR REPLACE VIEW v_asset_current_state AS
SELECT DISTINCT ON (asset_id) state_id, source_id, asset_id, observed_at, received_at,
       reported_state, availability, reason_code, message
FROM equipment_state_records
ORDER BY asset_id, observed_at DESC, state_id DESC;

CREATE OR REPLACE VIEW v_telemetry_valid AS
SELECT * FROM telemetry_records WHERE quality IS DISTINCT FROM 'BAD';

CREATE OR REPLACE VIEW v_data_clock AS
SELECT GREATEST((SELECT max(ts) FROM v_telemetry_valid), (SELECT max(ts) FROM event_records)) AS as_of;

CREATE OR REPLACE VIEW v_asset_overview AS
WITH clk AS (SELECT as_of FROM v_data_clock),
ev AS (
    SELECT er.asset_id,
           count(*) FILTER (WHERE er.severity = 'critical') AS critical_events_24h,
           count(*) FILTER (WHERE er.severity = 'warning') AS warning_events_24h,
           count(*) FILTER (WHERE et.name = 'heartbeat') AS heartbeats_24h
    FROM event_records er JOIN event_types et USING (event_type_id), clk
    WHERE er.ts > clk.as_of - interval '24 hours' AND er.ts <= clk.as_of GROUP BY er.asset_id),
tel AS (
    SELECT t.asset_id, count(*) AS telemetry_24h FROM v_telemetry_valid t, clk
    WHERE t.ts > clk.as_of - interval '24 hours' AND t.ts <= clk.as_of GROUP BY t.asset_id),
reports AS (
    SELECT asset_id FROM v_telemetry_valid GROUP BY asset_id
    UNION SELECT er.asset_id FROM event_records er JOIN event_types et USING (event_type_id) WHERE et.name = 'heartbeat'),
seen AS (
    SELECT asset_id, max(ts) AS last_seen FROM
    (SELECT asset_id, ts FROM v_telemetry_valid UNION ALL SELECT asset_id, ts FROM event_records) u GROUP BY asset_id),
alr AS (SELECT asset_id, count(*) AS open_alerts FROM alerts WHERE status <> 'resolved' GROUP BY asset_id)
SELECT a.asset_id, a.name AS asset_name, a.asset_type, st.code AS system, l.name AS zone, l.floor, b.name AS building,
       CASE WHEN COALESCE(ev.critical_events_24h, 0) > 0 THEN 'critical'
            WHEN a.asset_id IN (SELECT asset_id FROM reports)
                 AND COALESCE(tel.telemetry_24h, 0) = 0 AND COALESCE(ev.heartbeats_24h, 0) = 0 THEN 'no_data'
            WHEN COALESCE(ev.warning_events_24h, 0) > 0 THEN 'warning' ELSE 'normal' END AS health_24h,
       COALESCE(ev.critical_events_24h, 0) AS critical_events_24h,
       COALESCE(ev.warning_events_24h, 0) AS warning_events_24h,
       COALESCE(alr.open_alerts, 0) AS open_alerts, seen.last_seen
FROM assets a JOIN system_types st USING (system_type_id) JOIN locations l USING (location_id)
JOIN buildings b USING (building_id) LEFT JOIN ev USING (asset_id) LEFT JOIN tel USING (asset_id)
LEFT JOIN seen USING (asset_id) LEFT JOIN alr USING (asset_id);

INSERT INTO schema_migrations(version) VALUES ('006_ingestion_foundation')
ON CONFLICT (version) DO NOTHING;

COMMIT;
