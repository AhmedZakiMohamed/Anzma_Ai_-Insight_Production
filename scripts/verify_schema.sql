-- verify_schema.sql -- READ-ONLY catalog check of a live database against the authoritative schema
-- (base schema -> 003 -> 004 -> 005 -> 006 -> 007 -> 008; see DATABASE_BASELINE.md). Changes nothing: SELECT only.
-- Run:  psql -U anzma -d <db> -f verify_schema.sql        (every row must show status = OK)
-- Exit status of psql is not affected by a failed check; read the status column (final row = overall verdict).
WITH expected_tables(name) AS (VALUES
  ('buildings'),('locations'),('system_types'),('assets'),('metric_types'),('event_types'),
  ('metric_type_system_compat'),('event_type_system_compat'),('telemetry_records'),('event_records'),
  ('alerts'),('incidents'),('scenario_runs'),('schema_migrations'),('data_sources'),('source_credentials'),
  ('asset_external_ids'),('ingestion_batches'),('ingestion_rejections'),('equipment_state_records')),
expected_views(name) AS (VALUES
  ('v_data_clock'),('v_asset_overview'),('v_incident_overview'),('v_alert_overview'),('v_camera_status'),
  ('v_telemetry_valid'),('v_asset_current_state'),('v_unmapped_external_assets')),
expected_indexes(name) AS (VALUES
  ('idx_assets_location'),('idx_assets_system_type'),('idx_telemetry_ts'),('idx_event_ts'),('idx_event_asset_ts'),
  ('idx_alerts_first_ts'),('idx_alerts_asset_ts'),('idx_alerts_severity'),('idx_alerts_open'),
  ('idx_alerts_incident'),('idx_incidents_location_last'),('idx_incidents_status'),('uq_alerts_rule_asset_first_ts'),
  ('uq_buildings_building_code'),('idx_source_credentials_source_active'),('idx_asset_external_ids_asset'),
  ('uq_event_source_event_id'),('idx_telemetry_source_received'),('idx_event_source_received'),
  ('idx_equipment_state_asset_observed'),('uq_event_legacy_natural_key'),('idx_ingestion_rejections_unmapped')),
expected_columns(tbl, col) AS (VALUES
  ('alerts','incident_id'),('alerts','evidence'),('alerts','resolved_ts'),('alerts','explanation'),
  ('alerts','explanation_source'),('alerts','explained_at'),('incidents','grouping_rule'),
  ('telemetry_records','value'),('telemetry_records','quality'),('telemetry_records','source_id'),
  ('event_records','details'),('event_records','source_event_id'),('scenario_runs','expected_rules'),
  ('source_credentials','key_hash'),('asset_external_ids','external_id'),('ingestion_batches','payload_sha256'),
  ('ingestion_rejections','payload'),('ingestion_rejections','source_id'),('ingestion_rejections','external_id'),
  ('ingestion_batches','delivery_mode'),('ingestion_batches','connector_status'),('data_sources','max_backdate_days'),
  ('buildings','building_code'),
  ('data_sources','source_id'),('data_sources','source_code'),('data_sources','building_id'),
  ('data_sources','source_type'),('data_sources','connector_name'),('data_sources','connector_version'),
  ('data_sources','status'),('data_sources','source_metadata'),('data_sources','created_at'),
  ('data_sources','last_received_at'),('data_sources','last_success_at'),('data_sources','last_error_at'),
  ('data_sources','last_error'),('data_sources','max_backdate_days'),
  ('source_credentials','credential_id'),('source_credentials','source_id'),('source_credentials','key_prefix'),
  ('source_credentials','key_hash'),('source_credentials','scopes'),('source_credentials','created_at'),
  ('source_credentials','expires_at'),('source_credentials','revoked_at'),('source_credentials','last_used_at'),
  ('asset_external_ids','source_id'),('asset_external_ids','external_id'),('asset_external_ids','asset_id'),
  ('asset_external_ids','created_at'),
  ('telemetry_records','source_id'),('telemetry_records','received_at'),('telemetry_records','quality'),
  ('telemetry_records','ingestion_batch_id'),('telemetry_records','contract_version'),('telemetry_records','source_metadata'),
  ('event_records','source_id'),('event_records','received_at'),('event_records','source_event_id'),
  ('event_records','ingestion_batch_id'),('event_records','contract_version'),('event_records','source_metadata'),
  ('ingestion_batches','ingestion_batch_id'),('ingestion_batches','source_id'),('ingestion_batches','batch_id'),
  ('ingestion_batches','contract_version'),('ingestion_batches','payload_sha256'),('ingestion_batches','received_at'),
  ('ingestion_batches','sent_at'),('ingestion_batches','connector_name'),('ingestion_batches','connector_version'),
  ('ingestion_batches','received_count'),('ingestion_batches','accepted_count'),('ingestion_batches','duplicate_count'),
  ('ingestion_batches','rejected_count'),('ingestion_batches','response_payload'),
  ('ingestion_batches','delivery_mode'),('ingestion_batches','connector_status'),
  ('ingestion_rejections','rejection_id'),('ingestion_rejections','ingestion_batch_id'),
  ('ingestion_rejections','record_index'),('ingestion_rejections','record_type'),('ingestion_rejections','reason_code'),
  ('ingestion_rejections','message'),('ingestion_rejections','payload'),('ingestion_rejections','created_at'),
  ('ingestion_rejections','source_id'),('ingestion_rejections','external_id'),
  ('equipment_state_records','state_id'),('equipment_state_records','source_id'),('equipment_state_records','asset_id'),
  ('equipment_state_records','observed_at'),('equipment_state_records','received_at'),('equipment_state_records','reported_state'),
  ('equipment_state_records','availability'),('equipment_state_records','reason_code'),('equipment_state_records','message'),
  ('equipment_state_records','source_metadata'),('equipment_state_records','contract_version'),
  ('equipment_state_records','ingestion_batch_id'),('metric_types','min_plausible'),('metric_types','max_plausible')),
expected_constraints(name) AS (VALUES
  ('data_sources_pkey'),('data_sources_source_code_key'),('data_sources_source_code_check'),
  ('data_sources_building_id_fkey'),('data_sources_source_type_check'),('data_sources_status_check'),
  ('data_sources_max_backdate_days_check'),('source_credentials_pkey'),('source_credentials_source_id_fkey'),
  ('source_credentials_key_prefix_key'),('source_credentials_key_hash_check'),('source_credentials_scopes_check'),
  ('asset_external_ids_pkey'),('asset_external_ids_source_id_fkey'),('asset_external_ids_asset_id_fkey'),
  ('asset_external_ids_external_id_check'),('telemetry_records_quality_check'),
  ('telemetry_records_source_id_fkey'),('telemetry_records_ingestion_batch_id_fkey'),
  ('event_records_source_id_fkey'),('event_records_ingestion_batch_id_fkey'),
  ('ingestion_batches_pkey'),('ingestion_batches_source_id_fkey'),('ingestion_batches_source_id_batch_id_key'),
  ('ingestion_batches_payload_sha256_check'),('ingestion_batches_received_count_check'),
  ('ingestion_batches_accepted_count_check'),('ingestion_batches_duplicate_count_check'),
  ('ingestion_batches_rejected_count_check'),('ingestion_batches_check'),('ingestion_batches_delivery_mode_check'),
  ('ingestion_rejections_pkey'),('ingestion_rejections_ingestion_batch_id_fkey'),
  ('ingestion_rejections_record_index_check'),('ingestion_rejections_ingestion_batch_id_record_index_key'),
  ('ingestion_rejections_source_id_fkey'),('equipment_state_records_pkey'),
  ('equipment_state_records_source_id_fkey'),('equipment_state_records_asset_id_fkey'),
  ('equipment_state_records_ingestion_batch_id_fkey'),
  ('equipment_state_records_source_id_asset_id_observed_at_key'),
  ('equipment_state_records_reported_state_check'),('equipment_state_records_availability_check'),
  ('equipment_state_records_check'),('metric_types_plausible_range_check')),
checks AS (
  SELECT 1 AS n, 'tables missing' AS check_name, '0' AS expected,
         (SELECT count(*) FROM expected_tables e WHERE NOT EXISTS
            (SELECT 1 FROM information_schema.tables t WHERE t.table_schema='public' AND t.table_type='BASE TABLE' AND t.table_name=e.name))::text AS actual
  UNION ALL SELECT 2, 'unexpected extra tables (data-model.sql draft leaked in?)', '0',
         (SELECT count(*) FROM information_schema.tables t WHERE t.table_schema='public' AND t.table_type='BASE TABLE'
            AND t.table_name NOT IN (SELECT name FROM expected_tables))::text
  UNION ALL SELECT 3, 'views missing', '0',
         (SELECT count(*) FROM expected_views e WHERE NOT EXISTS
            (SELECT 1 FROM information_schema.views v WHERE v.table_schema='public' AND v.table_name=e.name))::text
  UNION ALL SELECT 4, 'indexes missing', '0',
         (SELECT count(*) FROM expected_indexes e WHERE NOT EXISTS
            (SELECT 1 FROM pg_indexes i WHERE i.schemaname='public' AND i.indexname=e.name))::text
  UNION ALL SELECT 5, 'key columns missing', '0',
         (SELECT count(*) FROM expected_columns e WHERE NOT EXISTS
            (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name=e.tbl AND c.column_name=e.col))::text
  UNION ALL SELECT 6, 'new constraints missing', '0',
         (SELECT count(*) FROM expected_constraints e WHERE NOT EXISTS
            (SELECT 1 FROM pg_constraint c JOIN pg_namespace n ON n.oid=c.connamespace
             WHERE n.nspname='public' AND c.conname=e.name))::text
  UNION ALL SELECT 7, 'asset/building mapping trigger missing', '1',
         (SELECT count(*) FROM pg_trigger t JOIN pg_class r ON r.oid=t.tgrelid JOIN pg_namespace n ON n.oid=r.relnamespace
          WHERE n.nspname='public' AND r.relname='asset_external_ids' AND t.tgname='trg_asset_external_ids_building'
            AND NOT t.tgisinternal)::text
  UNION ALL SELECT 8, 'legacy event natural key partial index missing/wrong', '1',
         (SELECT count(*) FROM pg_indexes WHERE schemaname='public' AND tablename='event_records'
          AND indexname='uq_event_legacy_natural_key' AND indexdef LIKE 'CREATE UNIQUE INDEX%(asset_id, event_type_id, ts)%'
          AND indexdef LIKE '%WHERE (source_event_id IS NULL)')::text
  UNION ALL SELECT 9, 'obsolete full legacy event unique constraint remains', '0',
         (SELECT count(*) FROM pg_constraint WHERE conrelid='public.event_records'::regclass
          AND conname='event_records_asset_id_event_type_id_ts_key')::text
  UNION ALL SELECT 10, 'alerts unique index is UNIQUE on (rule_id, asset_id, first_ts)', '1',
         (SELECT count(*) FROM pg_indexes WHERE schemaname='public' AND tablename='alerts' AND indexname='uq_alerts_rule_asset_first_ts'
            AND indexdef LIKE 'CREATE UNIQUE INDEX%(rule_id, asset_id, first_ts)')::text
  UNION ALL SELECT 11, 'duplicate alerts on (rule_id, asset_id, first_ts)', '0',
         (SELECT count(*) FROM (SELECT 1 FROM alerts GROUP BY rule_id, asset_id, first_ts HAVING count(*) > 1) d)::text
  UNION ALL SELECT 12, 'CHECK constraints in public (base schema + incidents)', '>=14',
         (SELECT count(*) FROM pg_constraint c JOIN pg_namespace n ON n.oid=c.connamespace WHERE n.nspname='public' AND c.contype='c')::text
  UNION ALL SELECT 13, 'UNIQUE constraints in public (telemetry/event/assets/etc.)', '>=8',
         (SELECT count(*) FROM pg_constraint c JOIN pg_namespace n ON n.oid=c.connamespace WHERE n.nspname='public' AND c.contype='u')::text
  UNION ALL SELECT 14, 'FOREIGN KEY constraints in public', '>=13',
         (SELECT count(*) FROM pg_constraint c JOIN pg_namespace n ON n.oid=c.connamespace WHERE n.nspname='public' AND c.contype='f')::text
  UNION ALL SELECT 15, 'source/building mapping validation function missing', '1',
         (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
          WHERE n.nspname='public' AND p.proname='validate_source_asset_building'
            AND pg_get_function_identity_arguments(p.oid)='')::text
)
SELECT n, check_name, expected, actual,
       CASE WHEN expected LIKE '>=%' THEN CASE WHEN actual::int >= substr(expected,3)::int THEN 'OK' ELSE 'FAIL' END
            WHEN actual = expected THEN 'OK' ELSE 'FAIL' END AS status
FROM checks
UNION ALL
SELECT 99, 'OVERALL', 'all OK', '', CASE WHEN bool_and(CASE WHEN expected LIKE '>=%' THEN actual::int >= substr(expected,3)::int ELSE actual = expected END) THEN 'OK' ELSE 'FAIL' END
FROM checks
ORDER BY n;
