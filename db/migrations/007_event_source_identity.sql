-- Permit multiple connector events at one asset/type/instant when they have
-- distinct source IDs, while retaining the legacy natural key for unscoped rows.
-- This is additive with respect to stored data: no event rows are rewritten/deleted.
BEGIN;

CREATE UNIQUE INDEX IF NOT EXISTS uq_event_legacy_natural_key
    ON event_records(asset_id, event_type_id, ts)
    WHERE source_event_id IS NULL;

ALTER TABLE event_records
    DROP CONSTRAINT IF EXISTS event_records_asset_id_event_type_id_ts_key;

INSERT INTO schema_migrations(version) VALUES ('007_event_source_identity')
ON CONFLICT (version) DO NOTHING;

COMMIT;
