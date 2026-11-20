-- Extend the E3 data-gap ledger with immutable detection context and lifecycle rules.
-- Additive and idempotent; existing gaps are backfilled without deleting history.
BEGIN;

ALTER TABLE assets
    ADD COLUMN IF NOT EXISTS lifecycle_status TEXT NOT NULL DEFAULT 'ACTIVE';

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='assets_lifecycle_status_check') THEN
        ALTER TABLE assets ADD CONSTRAINT assets_lifecycle_status_check
            CHECK (lifecycle_status IN ('ACTIVE','DISABLED','RETIRED'));
    END IF;
END $$;

ALTER TABLE data_gaps
    ADD COLUMN IF NOT EXISTS building_id BIGINT REFERENCES buildings(building_id) ON DELETE RESTRICT,
    ADD COLUMN IF NOT EXISTS expected_interval_seconds INTEGER,
    ADD COLUMN IF NOT EXISTS detected_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS opened_in_work_hours BOOLEAN,
    ADD COLUMN IF NOT EXISTS cause TEXT NOT NULL DEFAULT 'UNKNOWN',
    ADD COLUMN IF NOT EXISTS cause_updated_at TIMESTAMPTZ;

UPDATE data_gaps g
   SET building_id = COALESCE(g.building_id, l.building_id),
       expected_interval_seconds = COALESCE(
           g.expected_interval_seconds,
           (SELECT f.expected_interval_seconds FROM series_freshness_current f
             WHERE f.source_id IS NOT DISTINCT FROM g.source_id
               AND f.asset_id = g.asset_id
               AND f.record_kind = g.record_kind
               AND f.metric_code IS NOT DISTINCT FROM g.metric_code
               AND f.event_type IS NOT DISTINCT FROM g.event_type)),
       detected_at = COALESCE(g.detected_at, g.opened_at),
       opened_in_work_hours = COALESCE(
           g.opened_in_work_hours,
           (EXTRACT(ISODOW FROM (g.opened_at AT TIME ZONE COALESCE(NULLIF(b.timezone,''),'Etc/GMT-2')))::smallint
                <> ALL(COALESCE(b.non_working_weekdays, ARRAY[]::smallint[])))
           AND (g.opened_at AT TIME ZONE COALESCE(NULLIF(b.timezone,''),'Etc/GMT-2'))::time
                >= COALESCE(b.work_start_local, TIME '08:00')
           AND (g.opened_at AT TIME ZONE COALESCE(NULLIF(b.timezone,''),'Etc/GMT-2'))::time
                < COALESCE(b.work_end_local, TIME '18:00')),
       cause = COALESCE(g.cause, 'UNKNOWN'),
       cause_updated_at = COALESCE(g.cause_updated_at, g.opened_at, now())
  FROM assets a
  JOIN locations l USING (location_id)
  JOIN buildings b USING (building_id)
 WHERE a.asset_id = g.asset_id;

ALTER TABLE data_gaps
    ALTER COLUMN building_id SET NOT NULL,
    ALTER COLUMN detected_at SET NOT NULL,
    ALTER COLUMN opened_in_work_hours SET NOT NULL,
    ALTER COLUMN cause SET DEFAULT 'UNKNOWN',
    ALTER COLUMN cause SET NOT NULL,
    ALTER COLUMN cause_updated_at SET DEFAULT now(),
    ALTER COLUMN cause_updated_at SET NOT NULL;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='data_gaps_expected_interval_check') THEN
        ALTER TABLE data_gaps ADD CONSTRAINT data_gaps_expected_interval_check
            CHECK (expected_interval_seconds IS NULL OR expected_interval_seconds > 0);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='data_gaps_cause_check') THEN
        ALTER TABLE data_gaps ADD CONSTRAINT data_gaps_cause_check
            CHECK (cause IN ('UNKNOWN','NO_DATA','SOURCE_SUSPECTED'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='data_gaps_boundaries_check') THEN
        ALTER TABLE data_gaps ADD CONSTRAINT data_gaps_boundaries_check
            CHECK (gap_end IS NULL OR gap_end > gap_start);
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_data_gaps_one_open_per_series
    ON data_gaps (source_id, asset_id, record_kind, metric_code, event_type) NULLS NOT DISTINCT
    WHERE gap_end IS NULL;
CREATE INDEX IF NOT EXISTS idx_data_gaps_building_detected_at
    ON data_gaps (building_id, detected_at DESC);

CREATE OR REPLACE FUNCTION enforce_data_gap_lifecycle() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.building_id IS NULL OR NEW.detected_at IS NULL OR NEW.opened_in_work_hours IS NULL
           OR NEW.expected_interval_seconds IS NULL OR NEW.expected_interval_seconds <= 0
           OR NEW.cause IS NULL OR NEW.cause_updated_at IS NULL THEN
            RAISE EXCEPTION 'new data gaps require complete detection context'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;

    IF ROW(NEW.source_id, NEW.asset_id, NEW.building_id, NEW.record_kind,
           NEW.metric_code, NEW.event_type, NEW.gap_start, NEW.detected_at,
           NEW.expected_interval_seconds, NEW.opened_in_work_hours)
       IS DISTINCT FROM
       ROW(OLD.source_id, OLD.asset_id, OLD.building_id, OLD.record_kind,
           OLD.metric_code, OLD.event_type, OLD.gap_start, OLD.detected_at,
           OLD.expected_interval_seconds, OLD.opened_in_work_hours) THEN
        RAISE EXCEPTION 'data gap identity and detection boundaries are immutable'
            USING ERRCODE = '23514';
    END IF;

    IF OLD.gap_end IS NOT NULL AND
       ROW(NEW.gap_end, NEW.closed_at, NEW.closed_by) IS DISTINCT FROM
       ROW(OLD.gap_end, OLD.closed_at, OLD.closed_by) THEN
        RAISE EXCEPTION 'closed data gap boundaries are immutable'
            USING ERRCODE = '23514';
    END IF;
    IF OLD.gap_end IS NULL AND NEW.gap_end IS NOT NULL AND NEW.gap_end <= OLD.gap_start THEN
        RAISE EXCEPTION 'data gap closes only on an observation newer than gap_start'
            USING ERRCODE = '23514';
    END IF;

    IF NEW.cause IS DISTINCT FROM OLD.cause THEN
        IF OLD.gap_end IS NOT NULL OR NEW.gap_end IS NOT NULL THEN
            RAISE EXCEPTION 'cause is immutable after a data gap closes'
                USING ERRCODE = '23514';
        END IF;
        NEW.cause_updated_at := clock_timestamp();
    ELSIF NEW.cause_updated_at IS DISTINCT FROM OLD.cause_updated_at THEN
        RAISE EXCEPTION 'cause_updated_at changes only with cause'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='trg_data_gaps_lifecycle') THEN
        CREATE TRIGGER trg_data_gaps_lifecycle
            BEFORE INSERT OR UPDATE ON data_gaps
            FOR EACH ROW EXECUTE FUNCTION enforce_data_gap_lifecycle();
    END IF;
END $$;

COMMENT ON COLUMN data_gaps.expected_interval_seconds IS
    'Expectation snapshot at detection; NULL is permitted only for legacy rows where the historical expectation cannot be reconstructed.';
COMMENT ON COLUMN data_gaps.opened_in_work_hours IS
    'Informational local building schedule flag at detected_at; it never gates freshness or gap creation.';
COMMENT ON COLUMN data_gaps.cause IS
    'Evidence classification; E4a writes UNKNOWN and E4b may update it only while the gap is open.';

INSERT INTO schema_migrations(version) VALUES ('010_data_gap_lifecycle')
ON CONFLICT (version) DO NOTHING;

COMMIT;
