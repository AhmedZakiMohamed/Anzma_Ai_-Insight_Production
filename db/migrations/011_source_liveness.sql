-- E4b source liveness snapshots and auditable data-gap cause transitions.
-- Additive and safe for databases that already contain E3/E4a operational data.
BEGIN;

ALTER TABLE data_sources ADD COLUMN IF NOT EXISTS keepalive_interval_seconds INTEGER;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='data_sources_keepalive_interval_check') THEN
        ALTER TABLE data_sources ADD CONSTRAINT data_sources_keepalive_interval_check
            CHECK (keepalive_interval_seconds IS NULL OR keepalive_interval_seconds > 0);
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS source_liveness_current (
    source_id BIGINT PRIMARY KEY REFERENCES data_sources(source_id) ON DELETE RESTRICT,
    state TEXT NOT NULL,
    evaluated_at TIMESTAMPTZ NOT NULL,
    last_received_at TIMESTAMPTZ,
    expected_interval_seconds INTEGER,
    age_seconds BIGINT,
    reason_code TEXT NOT NULL,
    reason_message TEXT NOT NULL,
    active_series_count INTEGER NOT NULL DEFAULT 0,
    stale_series_count INTEGER NOT NULL DEFAULT 0,
    missing_series_count INTEGER NOT NULL DEFAULT 0,
    mass_stale BOOLEAN NOT NULL DEFAULT FALSE,
    last_success_at TIMESTAMPTZ,
    last_error_at TIMESTAMPTZ,
    last_error TEXT,
    connector_status JSONB,
    CONSTRAINT source_liveness_current_state_check CHECK (state IN ('ALIVE','SILENT','UNKNOWN','DISABLED')),
    CONSTRAINT source_liveness_current_interval_check CHECK (expected_interval_seconds IS NULL OR expected_interval_seconds > 0),
    CONSTRAINT source_liveness_current_age_check CHECK (age_seconds IS NULL OR age_seconds >= 0),
    CONSTRAINT source_liveness_current_counts_check CHECK
        (active_series_count >= 0 AND stale_series_count >= 0 AND missing_series_count >= 0
         AND stale_series_count + missing_series_count <= active_series_count)
);
CREATE INDEX IF NOT EXISTS idx_source_liveness_current_state_evaluated
    ON source_liveness_current(state,evaluated_at DESC);

CREATE TABLE IF NOT EXISTS source_liveness_transitions (
    transition_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id BIGINT NOT NULL REFERENCES data_sources(source_id) ON DELETE RESTRICT,
    evaluated_at TIMESTAMPTZ NOT NULL,
    from_state TEXT,
    to_state TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    active_series_count INTEGER NOT NULL,
    stale_series_count INTEGER NOT NULL,
    missing_series_count INTEGER NOT NULL,
    mass_stale BOOLEAN NOT NULL,
    CONSTRAINT source_liveness_transitions_from_state_check
        CHECK (from_state IS NULL OR from_state IN ('ALIVE','SILENT','UNKNOWN','DISABLED')),
    CONSTRAINT source_liveness_transitions_to_state_check
        CHECK (to_state IN ('ALIVE','SILENT','UNKNOWN','DISABLED')),
    CONSTRAINT source_liveness_transitions_counts_check
        CHECK (active_series_count >= 0 AND stale_series_count >= 0 AND missing_series_count >= 0
               AND stale_series_count + missing_series_count <= active_series_count),
    CONSTRAINT source_liveness_transitions_source_evaluation_key UNIQUE (source_id,evaluated_at)
);
CREATE INDEX IF NOT EXISTS idx_source_liveness_transitions_source_time
    ON source_liveness_transitions(source_id,evaluated_at DESC);

CREATE TABLE IF NOT EXISTS data_gap_cause_history (
    cause_history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    gap_id BIGINT NOT NULL REFERENCES data_gaps(gap_id) ON DELETE RESTRICT,
    old_cause TEXT NOT NULL,
    new_cause TEXT NOT NULL,
    changed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT data_gap_cause_history_old_check CHECK (old_cause IN ('UNKNOWN','NO_DATA','SOURCE_SUSPECTED')),
    CONSTRAINT data_gap_cause_history_new_check CHECK (new_cause IN ('UNKNOWN','NO_DATA','SOURCE_SUSPECTED')),
    CONSTRAINT data_gap_cause_history_change_check CHECK (old_cause <> new_cause)
);
CREATE INDEX IF NOT EXISTS idx_data_gap_cause_history_gap_time
    ON data_gap_cause_history(gap_id,changed_at DESC);

CREATE OR REPLACE FUNCTION audit_data_gap_cause_change() RETURNS trigger AS $$
BEGIN
    IF NEW.cause IS DISTINCT FROM OLD.cause THEN
        INSERT INTO data_gap_cause_history(gap_id,old_cause,new_cause,changed_at)
        VALUES (NEW.gap_id,OLD.cause,NEW.cause,COALESCE(NEW.cause_updated_at,clock_timestamp()));
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='trg_data_gap_cause_history') THEN
        CREATE TRIGGER trg_data_gap_cause_history
            AFTER UPDATE OF cause ON data_gaps
            FOR EACH ROW EXECUTE FUNCTION audit_data_gap_cause_change();
    END IF;
END $$;

COMMENT ON TABLE source_liveness_current IS
    'E4b evidence-only source liveness; it never changes asset health or creates alerts.';
COMMENT ON COLUMN data_sources.keepalive_interval_seconds IS
    'Optional per-source keepalive interval; NULL resolves to the GLOBAL source reporting expectation.';
COMMENT ON TABLE data_gap_cause_history IS
    'Append-only audit of cause transitions on open data gaps; causes freeze when the gap closes.';

INSERT INTO schema_migrations(version) VALUES ('011_source_liveness')
ON CONFLICT (version) DO NOTHING;
COMMIT;
