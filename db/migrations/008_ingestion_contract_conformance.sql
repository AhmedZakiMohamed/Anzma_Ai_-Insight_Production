-- Contract v1 validation metadata and audit fields. Additive; no historical facts are changed.
BEGIN;

ALTER TABLE data_sources
    ADD COLUMN IF NOT EXISTS max_backdate_days INTEGER
        CHECK (max_backdate_days IS NULL OR max_backdate_days BETWEEN 0 AND 36500);

ALTER TABLE ingestion_batches
    ADD COLUMN IF NOT EXISTS delivery_mode TEXT NOT NULL DEFAULT 'LIVE'
        CHECK (delivery_mode IN ('LIVE','BACKFILL'));
ALTER TABLE ingestion_batches
    ADD COLUMN IF NOT EXISTS connector_status JSONB;

ALTER TABLE ingestion_rejections
    ADD COLUMN IF NOT EXISTS source_id BIGINT REFERENCES data_sources(source_id) ON DELETE RESTRICT;
ALTER TABLE ingestion_rejections
    ADD COLUMN IF NOT EXISTS external_id TEXT;
CREATE INDEX IF NOT EXISTS idx_ingestion_rejections_unmapped
    ON ingestion_rejections(source_id, external_id, created_at)
    WHERE reason_code = 'ASSET_UNRESOLVED' AND external_id IS NOT NULL;

ALTER TABLE metric_types ADD COLUMN IF NOT EXISTS min_plausible DOUBLE PRECISION;
ALTER TABLE metric_types ADD COLUMN IF NOT EXISTS max_plausible DOUBLE PRECISION;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='metric_types_plausible_range_check') THEN
        ALTER TABLE metric_types ADD CONSTRAINT metric_types_plausible_range_check
            CHECK ((min_plausible IS NULL OR (min_plausible > '-Infinity'::float8 AND min_plausible < 'Infinity'::float8))
               AND (max_plausible IS NULL OR (max_plausible > '-Infinity'::float8 AND max_plausible < 'Infinity'::float8))
               AND (min_plausible IS NULL OR max_plausible IS NULL OR min_plausible <= max_plausible));
    END IF;
END $$;

CREATE OR REPLACE VIEW v_unmapped_external_assets AS
SELECT s.source_id, s.source_code AS source, r.external_id,
       count(*)::BIGINT AS count, min(r.created_at) AS first_seen, max(r.created_at) AS last_seen
FROM ingestion_rejections r
JOIN data_sources s USING (source_id)
WHERE r.reason_code='ASSET_UNRESOLVED' AND r.external_id IS NOT NULL
GROUP BY s.source_id, s.source_code, r.external_id;

INSERT INTO schema_migrations(version) VALUES ('008_ingestion_contract_conformance')
ON CONFLICT (version) DO NOTHING;

COMMIT;
