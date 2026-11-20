-- Additive schema for freshness expectations, current series values, and gap audit.
-- Existing facts are not rewritten; only existing building schedule fields are backfilled.
BEGIN;

ALTER TABLE buildings
    ADD COLUMN IF NOT EXISTS timezone TEXT,
    ADD COLUMN IF NOT EXISTS work_start_local TIME,
    ADD COLUMN IF NOT EXISTS work_end_local TIME,
    ADD COLUMN IF NOT EXISTS non_working_weekdays SMALLINT[];

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='buildings_timezone_nonempty_check') THEN
        ALTER TABLE buildings ADD CONSTRAINT buildings_timezone_nonempty_check
            CHECK (timezone IS NULL OR length(btrim(timezone)) > 0);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='buildings_work_hours_check') THEN
        ALTER TABLE buildings ADD CONSTRAINT buildings_work_hours_check
            CHECK (work_start_local IS NULL OR work_end_local IS NULL OR work_start_local < work_end_local);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='buildings_non_working_weekdays_check') THEN
        ALTER TABLE buildings ADD CONSTRAINT buildings_non_working_weekdays_check
            CHECK (non_working_weekdays IS NULL OR
                   (non_working_weekdays <@ ARRAY[1,2,3,4,5,6,7]::SMALLINT[] AND
                    array_position(non_working_weekdays, NULL::SMALLINT) IS NULL));
    END IF;
END $$;

UPDATE buildings
SET timezone = COALESCE(timezone, 'Etc/GMT-2'),
    work_start_local = COALESCE(work_start_local, TIME '08:00'),
    work_end_local = COALESCE(work_end_local, TIME '18:00'),
    non_working_weekdays = COALESCE(non_working_weekdays, ARRAY[]::SMALLINT[])
WHERE timezone IS NULL OR work_start_local IS NULL OR work_end_local IS NULL
   OR non_working_weekdays IS NULL;

CREATE TABLE IF NOT EXISTS reporting_expectations (
    expectation_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    scope TEXT NOT NULL,
    source_id BIGINT REFERENCES data_sources(source_id) ON DELETE RESTRICT,
    asset_id BIGINT REFERENCES assets(asset_id) ON DELETE RESTRICT,
    asset_type TEXT,
    record_kind TEXT NOT NULL,
    metric_code TEXT REFERENCES metric_types(name) ON UPDATE CASCADE ON DELETE RESTRICT,
    event_type TEXT REFERENCES event_types(name) ON UPDATE CASCADE ON DELETE RESTRICT,
    expected_interval_seconds INTEGER NOT NULL,
    stale_multiplier NUMERIC NOT NULL DEFAULT 2,
    missing_multiplier NUMERIC NOT NULL DEFAULT 5,
    priority INTEGER NOT NULL DEFAULT 100,
    valid_from TIMESTAMPTZ NOT NULL DEFAULT now(),
    valid_to TIMESTAMPTZ,
    expectation_version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT reporting_expectations_scope_check
        CHECK (scope IN ('ASSET_METRIC','ASSET_TYPE_METRIC','SOURCE','GLOBAL')),
    CONSTRAINT reporting_expectations_record_kind_check
        CHECK (record_kind IN ('telemetry','event','source')),
    CONSTRAINT reporting_expectations_scope_keys_check CHECK (
        (scope='GLOBAL' AND source_id IS NULL AND asset_id IS NULL AND asset_type IS NULL) OR
        (scope='SOURCE' AND source_id IS NOT NULL AND asset_id IS NULL AND asset_type IS NULL) OR
        (scope='ASSET_METRIC' AND source_id IS NULL AND asset_id IS NOT NULL AND asset_type IS NULL
            AND record_kind IN ('telemetry','event')) OR
        (scope='ASSET_TYPE_METRIC' AND source_id IS NULL AND asset_id IS NULL AND asset_type IS NOT NULL
            AND record_kind IN ('telemetry','event'))
    ),
    CONSTRAINT reporting_expectations_series_key_check CHECK (
        (record_kind='telemetry' AND event_type IS NULL AND
            (scope IN ('GLOBAL','SOURCE') OR (metric_code IS NOT NULL AND length(btrim(metric_code)) > 0))) OR
        (record_kind='event' AND metric_code IS NULL AND
            (scope IN ('GLOBAL','SOURCE') OR (event_type IS NOT NULL AND length(btrim(event_type)) > 0))) OR
        (record_kind='source' AND metric_code IS NULL AND event_type IS NULL AND scope IN ('GLOBAL','SOURCE'))
    ),
    CONSTRAINT reporting_expectations_asset_type_check
        CHECK (asset_type IS NULL OR length(btrim(asset_type)) > 0),
    CONSTRAINT reporting_expectations_interval_check
        CHECK (expected_interval_seconds > 0),
    CONSTRAINT reporting_expectations_stale_multiplier_check
        CHECK (stale_multiplier > 0 AND stale_multiplier < 'Infinity'::NUMERIC),
    CONSTRAINT reporting_expectations_missing_multiplier_check
        CHECK (missing_multiplier > stale_multiplier AND missing_multiplier < 'Infinity'::NUMERIC),
    CONSTRAINT reporting_expectations_priority_check
        CHECK (priority >= 0),
    CONSTRAINT reporting_expectations_version_check
        CHECK (expectation_version > 0),
    CONSTRAINT reporting_expectations_validity_check
        CHECK (valid_to IS NULL OR valid_from < valid_to),
    CONSTRAINT reporting_expectations_scope_priority_key
        UNIQUE NULLS NOT DISTINCT
        (scope, priority, source_id, asset_id, asset_type, record_kind, metric_code, event_type)
);

CREATE TABLE IF NOT EXISTS data_gaps (
    gap_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id BIGINT REFERENCES data_sources(source_id) ON DELETE RESTRICT,
    asset_id BIGINT NOT NULL REFERENCES assets(asset_id) ON DELETE RESTRICT,
    record_kind TEXT NOT NULL,
    metric_code TEXT REFERENCES metric_types(name) ON UPDATE CASCADE ON DELETE RESTRICT,
    event_type TEXT REFERENCES event_types(name) ON UPDATE CASCADE ON DELETE RESTRICT,
    gap_start TIMESTAMPTZ NOT NULL,
    gap_end TIMESTAMPTZ,
    opened_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_evaluated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_by TEXT,
    closed_at TIMESTAMPTZ,
    CONSTRAINT data_gaps_record_kind_check CHECK (record_kind IN ('telemetry','event')),
    CONSTRAINT data_gaps_series_key_check CHECK (
        (record_kind='telemetry' AND metric_code IS NOT NULL AND length(btrim(metric_code)) > 0 AND event_type IS NULL) OR
        (record_kind='event' AND event_type IS NOT NULL AND length(btrim(event_type)) > 0 AND metric_code IS NULL)
    ),
    CONSTRAINT data_gaps_closure_check CHECK (
        (gap_end IS NULL AND closed_by IS NULL AND closed_at IS NULL) OR
        (gap_end IS NOT NULL AND closed_by IS NOT NULL AND closed_at IS NOT NULL)
    ),
    CONSTRAINT data_gaps_series_gap_key
        UNIQUE NULLS NOT DISTINCT
        (source_id, asset_id, record_kind, metric_code, event_type, gap_start)
);

CREATE TABLE IF NOT EXISTS series_freshness_current (
    series_freshness_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id BIGINT REFERENCES data_sources(source_id) ON DELETE RESTRICT,
    asset_id BIGINT NOT NULL REFERENCES assets(asset_id) ON DELETE RESTRICT,
    record_kind TEXT NOT NULL,
    metric_code TEXT REFERENCES metric_types(name) ON UPDATE CASCADE ON DELETE RESTRICT,
    event_type TEXT REFERENCES event_types(name) ON UPDATE CASCADE ON DELETE RESTRICT,
    freshness TEXT NOT NULL,
    last_observed_at TIMESTAMPTZ,
    last_received_at TIMESTAMPTZ,
    age_seconds BIGINT,
    expected_interval_seconds INTEGER NOT NULL,
    evaluated_at TIMESTAMPTZ NOT NULL,
    expectation_id BIGINT NOT NULL REFERENCES reporting_expectations(expectation_id) ON DELETE RESTRICT,
    expectation_version INTEGER NOT NULL,
    CONSTRAINT series_freshness_current_record_kind_check CHECK (record_kind IN ('telemetry','event')),
    CONSTRAINT series_freshness_current_series_key_check CHECK (
        (record_kind='telemetry' AND metric_code IS NOT NULL AND length(btrim(metric_code)) > 0 AND event_type IS NULL) OR
        (record_kind='event' AND event_type IS NOT NULL AND length(btrim(event_type)) > 0 AND metric_code IS NULL)
    ),
    CONSTRAINT series_freshness_current_state_check
        CHECK (freshness IN ('UNKNOWN','FRESH','STALE','MISSING')),
    CONSTRAINT series_freshness_current_observation_check CHECK (
        (freshness='UNKNOWN' AND last_observed_at IS NULL) OR
        (freshness<>'UNKNOWN' AND last_observed_at IS NOT NULL)
    ),
    CONSTRAINT series_freshness_current_age_check CHECK (age_seconds IS NULL OR age_seconds >= 0),
    CONSTRAINT series_freshness_current_interval_check CHECK (expected_interval_seconds > 0),
    CONSTRAINT series_freshness_current_version_check CHECK (expectation_version > 0),
    CONSTRAINT series_freshness_current_series_key
        UNIQUE NULLS NOT DISTINCT
        (source_id, asset_id, record_kind, metric_code, event_type)
);

INSERT INTO reporting_expectations
    (scope, record_kind, event_type, expected_interval_seconds,
     stale_multiplier, missing_multiplier, priority)
VALUES
    ('GLOBAL','telemetry',NULL,900,2,5,0),
    ('GLOBAL','event','heartbeat',3600,2,5,0),
    ('GLOBAL','source',NULL,300,2,5,0)
ON CONFLICT ON CONSTRAINT reporting_expectations_scope_priority_key DO NOTHING;

INSERT INTO schema_migrations(version) VALUES ('009_freshness_expectations')
ON CONFLICT (version) DO NOTHING;

COMMIT;
