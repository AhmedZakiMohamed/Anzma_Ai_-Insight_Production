# DATABASE_BASELINE.md — single source of truth for the Anzma database

Status legend: this document records the **pre-006 deployed baseline** and historical results below. The repository now adds migrations 006–007; their schema and upgrade procedure are documented in [DATA_MIGRATION_PLAN.md](DATA_MIGRATION_PLAN.md). The shared running database remains at its inspected pre-006 version until an operator applies them.

## 1. Authoritative architecture

PostgreSQL 16 (Docker Compose, `postgres:16`). The pre-006 deployed baseline is **13 tables + 5 views**. New installs add migration 006 for **20 tables + 7 views** and migration 007 for event uniqueness indexes (table/view counts unchanged). Existing volumes require the explicit migration runner; Compose init scripts alone do not upgrade them.

| # | File | Adds | State |
|---|---|---|---|
| 1 | `db/init/anzma_schema_and_seed.sql` | 12 tables (reference, facts, alerts, scenario_runs) + seed | tracked; clean install exit 0 (evidence) |
| 2 | `db/migrations/003_incidents.sql` | `incidents`, `alerts.incident_id`, 3 indexes | present; idempotent by design |
| 3 | `db/migrations/004_chat_views.sql` | 5 views `v_*` | present; idempotent by design |
| 4 | `db/migrations/005_alert_uniqueness.sql` | UNIQUE index `uq_alerts_rule_asset_first_ts` | present; verified on the fresh isolated test database: index exists and `verify_schema.sql` reports OVERALL OK (tested commit 441110d; see `test_logs/441110d/schema.txt`) |
| 5 | `db/migrations/006_ingestion_foundation.sql` | source registry/key hashes, mappings, nullable provenance, batch/rejection audit, state facts, two views | implemented; migration and constraints passed in isolated test DB; not applied to shared DB |
| 6 | `db/migrations/007_event_source_identity.sql` | replaces full legacy event unique constraint with source-aware partial keys | implemented; distinct source IDs and legacy duplicates tested in isolated DB; not applied to shared DB |

Not authoritative: `data-model.sql` (22-table draft, not loaded by Compose). `db/init/anzma_schema_and_seed.sql` is the single bootstrap schema-and-seed file; tests use this path directly.

## 2. Pre-006 tables (13)

`buildings`, `locations` (UNIQUE building_id+name), `system_types` (CHECK code in HVAC/NETWORKING/ACCESS_CONTROL/CCTV/FIRE_ALARM), `assets` (asset_code UNIQUE; FK system_type, location), `metric_types` (name UNIQUE, unit), `event_types` (name UNIQUE), `metric_type_system_compat`, `event_type_system_compat` (composite PKs), `telemetry_records` (UNIQUE asset+metric+ts; CHECK value not NaN/±Infinity), `event_records` (UNIQUE asset+event_type+ts; CHECK severity), `alerts`, `incidents` (003), `scenario_runs` (CHECK end_ts ≥ start_ts; array columns without FKs).

`alerts`: CHECK detection_method ∈ threshold/anomaly/compound; severity ∈ info/warning/critical; status ∈ open/acknowledged/resolved; explanation_source ∈ ollama/fallback; `last_ts ≥ first_ts`; `(status='resolved') = (resolved_ts IS NOT NULL)`; FK asset_id; FK incident_id (003); **UNIQUE (rule_id, asset_id, first_ts) from 005**. `incidents` carries the same last_ts and resolved checks.

Indexes: idx_assets_location, idx_assets_system_type, idx_telemetry_ts, idx_event_ts, idx_event_asset_ts, idx_alerts_first_ts, idx_alerts_asset_ts, idx_alerts_severity, idx_alerts_open (partial, status<>'resolved'), idx_alerts_incident, idx_incidents_location_last, idx_incidents_status, uq_alerts_rule_asset_first_ts.

## 3. Pre-006 views (5, from 004; read-only)

`v_data_clock(as_of)`, `v_asset_overview`, `v_incident_overview`, `v_alert_overview`, `v_camera_status`. "Now" in every view is the **latest stored record** (`v_data_clock`), never the server clock. Column lists are asserted in `day2_ahmed/test_data_integrity.py`.

## 4. Data integrity findings (VERIFIED (evidence), commit 87e084d, DB snapshot of 2026-11-16)

- Row counts: buildings 2, locations 6, system_types 5, assets 12, metric_types 8, event_types 12, metric compat 8, event compat 16, telemetry 4669, events 959, alerts 29, incidents 13, scenario_runs 0. A clean install has 955 events, 0 alerts, 0 incidents (`catalog_diff.txt`).

- Orphans: 0 (telemetry/events/alerts). Compatibility violations: 0. Duplicate alerts by (rule_id, asset_id, first_ts): 0 rows (section E), so 005 can be applied to that database.

- Before 005 the database had **no** unique constraint on alerts; uniqueness lived only in `store_alerts` (`WHERE NOT EXISTS`) → race between the detection loop and `POST /alerts` / `/alerts/run`.

- `*_compat` tables are **not enforced** by the database (no FK/trigger from telemetry/events). Enforcement is application-level (`integrity_rules.py`, `validation.py`). `test_data_integrity.py` pins this current behaviour.

- Building is derived through one path only (assets → locations → buildings), so an asset cannot be attached to a wrong building. Adding a duplicated `building_id` column would make that possible (reason to defer it).

## 5. Timestamp / data-clock finding (DOCUMENTED, no change made)

Evidence (`integrity_output.txt`, 2026-11-16 ~10:50 UTC): data clock `as_of` = 2026-11-11 08:00:02+00, wall clock 5 d 02:50 later. Cause visible in the event list: three `hvac_fault` events (ids 957–959, `{"note":"manual test"}`) were inserted manually at 2026-11-11 08:00:00–02 for HVAC-AHU-01, after the seeded data ends (telemetry last 2026-11-10 12:45). Because "now" = max record, the clock jumped ~19 h, so the other 11 assets show `age_vs_data_clock` of 19 h–1 d and can be classified `no_data` by `v_asset_overview`. Impact: dashboard/chat health and the 20-min/1-h gap rules depend on the latest record. **Decision pending (owner Amr):** rebuild the demo DB from the seed, or define the clock differently. Not changed here.

## 6. Bandwidth unit fix (implemented in current working tree; verification pending)

`bandwidth_util` is stored as `%`; this fix keeps that canonical unit because the model has no per-link capacity to convert utilisation to Mbps. The code uses provisional thresholds of 80% warning and 90% critical mean, a 15 percentage-point minimum anomaly deviation, percent-based alert facts/summaries, and a percent-based packet-loss playbook comparison. These values are initial operating defaults, not capacity-calibrated limits. Automated regression passed 138/138 across unit and integration suites; `test_dashboard.py` exited 0; S1 passed 14/14 on fallback and 15/15 with live Ollama; schema verification reported `OVERALL OK`. The seeded dataset still produces 10 alerts / 8 incidents and no bandwidth-high or bandwidth-anomaly alert. Its maximum utilization is 72.32% (below the warning threshold), and the seed does not contain a sustained 15-point bandwidth deviation. A synthetic packet-loss case at 85% now selects congestion; the seeded case at 68.6% remains link quality. Historical measurements in §4 and prior verification claims refer to earlier commits and do not verify this change.

Other limitations: telemetry/event tables have no `received_at`, `source`, `quality`; no timezone on buildings/locations; alerts never UPDATE first_ts/last_ts; no link-capacity data exists to calibrate the provisional bandwidth thresholds.

## 7. Clean install and deployment

1. Compose runs init files only on an **empty** volume. The historical 2026-11-17 verification used migrations through 005 and confirmed the baseline schema (13 tables, 5 views). Current Compose adds 006–007 for new volumes.

2. Existing databases are not upgraded by Compose init mounts. Use `scripts/apply_migrations.py` for unapplied migrations 006+ after backup and operator review. The shared DB was not migrated during implementation.

3. 005 aborts with a clear message if duplicates exist (it never deletes data).

4. Verify any database (read-only): `psql -U <user> -d <db> -f verify_schema.sql` → every row `OK`, final row `OVERALL OK`. **Executed on the fresh test volume: OVERALL OK** (see [`test_logs/441110d/schema.txt`](../test_logs/441110d/schema.txt)).

## 8. Deferred (not implemented)

Freshness/gap tracking, retention/rollups, organizations, `building_id` duplicated onto facts, `alert_evidence`, unit conversions, relaxing the `system_types.code` CHECK, a data-clock change (§5), and calibration of provisional bandwidth thresholds remain deferred. Ingestion sources, mappings, credentials, provenance, quality, batch/rejection audit, state facts, and the migration runner are implemented by 006 and its companion code.

## 9. Test status

Latest verified (tested commit `441110d`, 2026-11-17): 25 + 43 + 18 + 18 + 31 = **135/135 PASS**; schema `OVERALL OK`; readiness PASS; `e2e_s1.py` PASS 14/14; read-only `test_dashboard.py` PASS. Live Ollama explanation and separate fallback were both verified on alert 7; the Compose-container Ollama model-pull path was not run. See [DAY2_TEST_RESULTS.md](DAY2_TEST_RESULTS.md).

## 10. Task 3 analysis (static audit of code and Ahmed's reported evidence)

The path consolidation in §1 and §7 was verified on commit `441110d` (2026-11-17): a fresh Compose install was executed in a separate project (`anzma-acceptance-441110d`, fresh volume), and the database was verified to contain migration 005. See [DAY2_TEST_RESULTS.md](DAY2_TEST_RESULTS.md) and [`test_logs/441110d/schema.txt`](../test_logs/441110d/schema.txt).

| Topic | What the code/schema actually does | Minimum change for Core v0.1 |
|---|---|---|
| Source / asset identification | Asset = `assets.asset_id` (numeric, what the API uses) + `asset_code` (UNIQUE, e.g. `HVAC-AHU-01`) + `name`. No vendor/external id; no `source` on telemetry, events or alerts. Manual origin is only visible in `event_records.details` (`manual test`) and `alerts.evidence.source = "manual"`. | None now. Treat `asset_code` as the stable external key. `asset_external_ids` / `data_sources` stay PROPOSED. |
| Timestamps | All `ts` columns are `TIMESTAMPTZ`; `db_adapter` converts to UTC; no `received_at` (cannot tell event time from arrival time). | None now (`received_at` deferred until a connector exists). |
| Measurement consistency | `metric_types.unit` holds the unit; telemetry CHECK rejects NaN/±Infinity. `bandwidth_util` is `%`; alert thresholds, facts, summaries, anomaly labels, and playbooks now use percent. | Regression suites pass on this change; limits still need capacity-based calibration when that data exists. |
| Duplicate prevention | telemetry UNIQUE(asset,metric,ts); events UNIQUE(asset,event_type,ts) and `POST /events` uses `ON CONFLICT DO NOTHING` → HTTP 409; alerts: **005**. Incidents: grouping logic in `incident_engine.py`, no DB uniqueness. | 005 is in the clean-install sequence and verified on the fresh test database (commit `441110d`, 2026-11-17). Clean-install verification also passed; see [DAY2_TEST_RESULTS.md](DAY2_TEST_RESULTS.md). |
| Compatibility rules | `*_compat` tables are not enforced by the DB. `POST /events` enforces event/system compatibility in code; no code path inserts telemetry (only the seed does). | None; pinned by a test. |
| Equipment state | Not stored. Computed at query time: `v_asset_overview.health_24h` = critical > no_data > warning > normal over the 24 h before the data clock; cameras by latest `camera_offline` vs `camera_online` (`v_camera_status`). | None. `asset_state_current` stays PROPOSED. |
| Freshness | "Now" = `max(ts)` of telemetry/events (`v_data_clock`, `da.latest_ts`), never the wall clock; `no_data` and the 20-min reading-gap rule (`reading_gap_min=20`) depend on it. A manual event moves the clock (§5). | None; decision pending (§5). |
| Current vs historical | History = `event_records` + `telemetry_records` (append-only facts); current status is derived from them; alerts/incidents have their own lifecycle. | None. |

### Audit findings added in this session

1. `day2_ahmed/e2e_s1.py` built its database from base + 003 only. With the new `ON CONFLICT` in `store_alerts` it would fail without 005. **Fixed** (now applies 003, 004, 005).

2. `day2_ahmed/alert_engine.py::store_alerts(cur, alerts, start, end)` is legacy: it `DELETE`s alerts in a window and re-inserts them with no conflict handling. Nothing calls it (grep: only `pipeline.py` and `App.py` call `db_adapter.store_alerts`). **Not changed**; recommend removing it in a separate change because it would orphan `alerts.incident_id` links if ever used.

3. Contradictory schema references. **Fixed:** `DATA_MODEL.md` and `data-model.sql` carry a DRAFT / NOT AUTHORITATIVE banner. An earlier audit note incorrectly said README and RUN_DEMO omitted migrations 004 and 005; that claim is removed because those references do not occur in the current guides. `DECISION_MATRIX.md` has a historical plan reference to `migrations/001_unified_schema.sql`.

4. Tests use the single bootstrap file at `db/init/anzma_schema_and_seed.sql`; no duplicate schema copy is required.
