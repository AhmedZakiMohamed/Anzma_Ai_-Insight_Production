# DATA_MIGRATION_PLAN.md — Anzma AI Insight Core

| | |
|---|---|
| **Status** | Migrations `006_ingestion_foundation.sql`, `007_event_source_identity.sql`, and `scripts/apply_migrations.py` are implemented. They have not been applied to the shared existing database. Remaining units below are future design guidance unless marked implemented. |
| **Principle** | Incremental, additive, reversible. Existing tables, columns, views and API shapes keep working at every step. |
| **Related** | `CORE_DATA_ARCHITECTURE.md`, `CANONICAL_DATA_MODEL.md`, `HISTORIAN_DESIGN.md`, `SPRINT_1_TECHNICAL_ISSUES.md` |

---

## 1. Current-to-target mapping

Format: **Current structure → Proposed canonical structure → Required adaptation.** “None” means existing code keeps working unchanged.

### 1.1 Tables and columns

| Current (verified) | Proposed canonical | Required adaptation |
|---|---|---|
| `buildings(building_id, name, building_code)` | Migration 006 adds nullable `building_code`; other organization/timezone fields remain future proposals | Assign a unique code to each building before source provisioning. |
| *(none)* | `organizations` | One default row; backfill `buildings.organization_id` |
| `locations(location_id, building_id, floor, name)` | + `parent_location_id`, `location_type`, `zone_type`, `is_sensitive` | None. `'Server Room'` string checks (`alert_engine.py`, `chat_lite.py`) later read `zone_type` |
| `system_types(code CHECK …)` | same codes; CHECK dropped | None. `reference_data.SYSTEM_CODES` later read from the table |
| `assets(asset_id, asset_code, name, asset_type, system_type_id, location_id)` | + `building_id`, `parent_asset_id`, `manufacturer`, `model`, `serial_number`, `lifecycle_status`, `installed_at`, `metadata` | None. `building_id` backfilled from `locations` |
| Migration 006 | `asset_external_ids`, `data_sources`, `source_credentials` | New; source rows are provisioned explicitly; no legacy source backfill |
| `metric_types(metric_id, name, unit)` | + `value_kind`, `min_plausible`, `max_plausible`; `unit` = canonical unit | `bandwidth_util` remains canonical `%` in the current runtime; thresholds are provisional until per-link capacity data exists. |
| `event_types(event_type_id, name)` | + `event_class`, `resolves_event_type_id` | Later replaces `FAULT_RECOVERY_PAIRS`, `CLOSERS`, `v_camera_status` pair logic (Phase M6, parity-tested) |
| `metric_type_system_compat`, `event_type_system_compat` | unchanged; enforced by the ingestion service | `integrity_rules.EVENT_CATALOG` (Python duplicate) retired later |
| `telemetry_records(telemetry_id, asset_id, metric_id, ts, value)` | Migration 006 adds nullable `source_id`, `received_at`, `quality`, `ingestion_batch_id`, `contract_version`, `source_metadata` | Existing rows remain unchanged with added fields NULL. |
| `event_records(event_id, asset_id, event_type_id, ts, severity, details)` | Migration 006 adds nullable source, event ID, receive time, batch, contract, and metadata columns | Migration 007 replaces the full natural key with a partial legacy key for rows with NULL `source_event_id`; source events use the scoped source-event unique index. |
| `alerts(…, evidence JSONB, incident_id)` | + `building_id`, `detected_at`, `acknowledged_ts/by`, `resolved_by`, `rule_version`; UNIQUE `(rule_id, asset_id, first_ts)` | `db_adapter.store_alerts`: `NOT EXISTS` → `ON CONFLICT DO NOTHING` (same semantics) |
| `alerts.evidence` JSON (`ref`, `source`, `record_id`, …) | `alert_evidence` rows (dual-write) | `evidence.py` keeps reading JSON until parity is proven |
| `incidents(location_id, …)` | + `building_id` | None |
| Migration 006 | `equipment_state_records`, `ingestion_batches`, `ingestion_rejections` | Implemented; append-only accepted state facts and rejection audit. Freshness/gaps and retention remain future work. |
| `scenario_runs` | later moved to schema `eval` | None; no code references it |
| Views `v_data_clock`, `v_asset_overview`, `v_incident_overview`, `v_alert_overview`, `v_camera_status` | same names/columns + `building_id` | Chat keeps working (`chat_lite.py` lists these views in its prompt). `v_asset_overview` health rule reconciled with the KPI in M6 (§9.4) |

### 1.2 Consumers and the adaptation they need

| Consumer | Evidence | Compatibility assessment |
|---|---|---|
| KPI engine `app/repositories/dashboard_core_pg.py` | SQL on `assets, system_types, locations, event_records, event_types, telemetry_records, metric_types`; constants `temperature`, `packet_loss`, `bandwidth_util`, `heartbeat` | **Compatible without change** through additive schema phases. Time windows `[start, end)`; building filter derives through `locations.building_id` |
| Alert engine `app/domain/alerts/alert_engine.py` + `app/adapters/database/db_adapter.py` | reads assets, events, and telemetry through the database adapter | **Compatible** if legacy columns remain. Timezone/zone semantics remain future work; current bandwidth rules use `%`. A future quality filter must retain today's outputs for existing rows. |
| Incident engine `app/domain/incidents/incident_engine.py` | alerts, incidents, assets, locations; grouping rule `same_asset_or_system_within_15_min` | Preserve grouping and lifecycle; any building scoping is future work. |
| Evidence `app/domain/evidence/evidence.py` | `alerts.evidence` JSON; re-reads events/telemetry by existing record IDs | Preserve IDs, values and evidence JSON; normalized evidence is only a proposal. |
| Validation / AI `app/domain/evidence/validation.py`, `app/adapters/model_providers/ai_explainer.py`, `app/domain/ai/recommendations.py` | alerts, evidence and approved playbooks | Preserve grounding and recommendation rules. |
| Chat `app/api/chat_lite.py`, `app/api/chat_facts.py` | compatibility views, KPI functions and read-only query/tool flow | Preserve existing routes and output behavior; building-scoped queries remain unimplemented. |
| Dashboard `app/api/frontend_api.py`, `app/dashboard/static/dashboard.html` | dashboard queries and response shapes | Preserve API shapes while extending storage. |
| Tests | `tests/unit/test_day2_ahmed.py`; integration tests under `tests/integration/`; scenario under `tests/scenarios/` | Run in an isolated disposable database; do not point destructive test setup at the shared application volume. |

---

## 2. Existing structures that will be retained

All pre-006 tables and views, existing columns, and legacy API routes remain. Migration 007 intentionally replaces the event natural-key constraint with a partial index so source-ID events can share a timestamp; the legacy API still uses and enforces that natural key for rows without source IDs. Fresh database initialization adds 006–007; existing databases use the separate migration runner.

## 3. Implemented and proposed structures

Migrations 006–007 implement `data_sources`, `source_credentials`, `asset_external_ids`, `ingestion_batches`, `ingestion_rejections`, `equipment_state_records`, `schema_migrations`, provenance/quality columns, and views `v_telemetry_valid` and `v_asset_overview`. The partial event indexes are described in §4. Still proposed: `organizations`, `reporting_expectations`, `asset_state_current`, `asset_state_history`, `data_gaps`, `alert_evidence`, `retention_policies`, optional `unit_conversions`, `v_telemetry_canonical`, `v_event_canonical`, `v_series_freshness`, and measured-need-only rollups. Definitions: `CANONICAL_DATA_MODEL.md`.

## 4. Schema changes required (by migration unit)

The repository already uses `005_alert_uniqueness.sql`; ingestion foundation is implemented in 006–007. Migrations 006 and 007 are the implemented ingestion units. The remaining sequencing table is a future design sketch and does not authorize additional DDL operations.

| Unit | Contents | Locking / risk |
|---|---|---|
| `006_ingestion_foundation.sql` **Implemented** | migration ledger; building code; source/key registry; source-scoped asset mappings; nullable provenance and quality fields; batch/rejection audit; state facts; quality-aware views | Additive, no historical fact updates or deletes; not yet applied to shared DB |
| `007_event_source_identity.sql` **Implemented** | source-event identity unique index; partial legacy natural-key index; drops only the redundant unique constraint, never rows | Additive index change; no historical rows updated/deleted; not yet applied to shared DB |
| `008+` future | Organizations and other optional canonical-model structures | Requires separate approval; outside current scope |
| `009_backfill.sql` (or script) | deterministic building/source/quality backfills and evidence references | Batched; only new columns; source mapping must be deterministic |
| `010_constraints.sql` | validated constraints and indexes | Preflight duplicates and data consistency; index deployment method depends on runner |
| `011_state_and_expectations.sql` | current/history state, reporting expectations, data gaps | Separate approved state/freshness semantics required |
| `012_views.sql` | compatibility and building-aware views | Preserve existing names and response behavior |
| `013_ingestion_audit.sql` | batch and rejection audit, disabled retention metadata if approved | Depends on finalized ingestion contract |

**Pitfall to avoid (specific to this schema):** `ALTER TABLE … ADD COLUMN received_at TIMESTAMPTZ DEFAULT now()` would stamp every **existing** row with the migration time, falsely claiming it was received then. Add the column without a default, leave legacy rows `NULL`, and only afterwards `SET DEFAULT now()` for new inserts.

**Duplicate pre-check before `UNIQUE (rule_id, asset_id, first_ts)` on `alerts`:**
`SELECT rule_id, asset_id, first_ts, count(*) FROM alerts GROUP BY 1,2,3 HAVING count(*) > 1;` — if rows exist, resolve them deliberately (keep the oldest `alert_id`, re-point `incident_id`/explanations, record the decision); do not delete silently.

## 5. Compatibility strategy

1. **Additive only in Sprint 1:** no column dropped/renamed; no type changes.
2. **Compatibility views:** old names and semantics preserved; new `building_id` columns appended at the end of view column lists (CREATE OR REPLACE VIEW permits appending columns).
3. **Adaptation layer:** `db_adapter` remains the only place that translates names/enums; new code (ingestion, repository) lives beside it and reuses `guard`, `Json`, `DatabaseUnavailable`.
4. **Dual-write for evidence:** `alerts.evidence` JSON stays authoritative for reads until `alert_evidence` parity is proven (count and value equality per alert), then reads may switch.
5. **Feature flags:** ingestion endpoint, state/freshness job, and any behaviour-changing option are switched by configuration; default = off/legacy behaviour.
6. **Shadow computation:** new state/freshness values are computed and compared with the KPI’s status counts (mismatches logged) before any output depends on them.
7. **Backward-compatible API:** response fields are only added, never removed or renamed.
8. **Local changes protection:** preserve existing chat routes and old ingestion APIs; the approved quality filter routes telemetry readers through `v_telemetry_valid`.

## 6. Migration phases

| Phase | Goal | Changes behaviour? |
|---|---|---|
| **M0 Baseline** | Backup + golden snapshot (§9) on a scratch copy; restore rehearsal | No |
| **M1 Foundation (006–007)** | Ingestion ledger, source/building/asset identity, nullable fact provenance, batch/rejection audit, source-aware event keys | New ingestion route only; legacy event key retained for unscoped rows |
| **M2 Backfill (008)** | `building_id`, `source_id`, `quality`, `alert_evidence`; `received_at` stays NULL | No |
| **M3 Constraints & views (009, 011)** | Validate FKs; defaults for new rows; compat views | No (new rows only) |
| **M4 Ingestion (012 + new code)** | Canonical contract endpoint behind a flag; `POST /events` unchanged | No for existing flows |
| **M5 State & freshness (010 + job)** | Shadow-compute state/freshness; expose alongside existing KPI; per-building timezone/zone_type/expectations introduced behind flags | Only where a flag is turned on and approved |

### 6.1 Applying the implemented migration to an existing deployment

After deploying the code containing migrations 006–007 and taking the normal database backup, an operator can run:

```sh
docker compose --env-file .env -p anzma-insight -f deploy/compose/docker-compose.yml exec -T api python scripts/apply_migrations.py
```

The runner takes a PostgreSQL advisory lock, applies only unrecorded additive migrations numbered 006 or later, and records each version in `schema_migrations`. It does not run seed SQL, backfill source identities, modify/delete historical fact rows, or rotate credentials. Review the migration file and confirm the target database/backup before running it. `register-building` must be used to assign `building_code` before provisioning a source. Fresh Compose volumes receive migrations 006–007 from the init mounts; existing volumes do not.

The compatible rollback is to stop using the v1 route and deploy code that does not require it. Keep migration-created tables and columns in place; do not drop them or clear populated audit/fact data. Existing ingestion APIs remain available.
| **M6 Consolidation** | One asset-health definition, one pair definition, one connection module; remove legacy files and dead code; mark superseded docs | Only where parity tests show differences that were explicitly approved |

Gates between phases: all existing tests green; reconciliation (§10) clean; golden snapshot diff empty (except approved changes).

## 7. Data preservation strategy

- **Immutability of history:** no `UPDATE` to `ts`, `value`, `severity`, `details`, `rule_id`, `first_ts`, `last_ts`, `evidence`, `explanation`, statuses or timestamps of existing rows. Only **new** columns are written.
- **Identity preservation:** no `telemetry_id`, `event_id`, `alert_id`, `incident_id` changes — evidence refs depend on them (`evidence.py` re-reads by id).
- **Unknowns stay unknown:** `received_at` = NULL for legacy rows; source = `legacy-seed`; no invented provenance.
- **Provenance marking:** legacy rows are distinguishable (`source_id → legacy-seed`) so reports can separate simulated from connector data.
- **Backups:** `pg_dump` before M1 and before each later phase, stored outside the Docker volume; restore tested at M0.
- **Backfill batching:** id-range batches, each in its own transaction, resumable; progress table or log.

## 8. Rollback strategy

| Phase | Rollback |
|---|---|
| M1 | Roll back application use first. Leave additive columns/tables in place if they may contain data; dropping them requires a separately reviewed backup, reconciliation, and explicit data-disposition plan. |
| M2 | Stop new writes and roll back readers. Preserve populated provenance/backfill values; do not reset to NULL merely to mimic the old schema. |
| M3 | Revert compatible views/constraints only when no deployed code depends on them. Retain any new facts and audit rows. |
| M4 | Turn the feature flag off; canonical facts and rejection/batch audit remain stored. Never delete rows as an application rollback. |
| M5 | Turn flags off; new state tables ignored by legacy code |
| M6 | Release-by-release: keep legacy code one release after replacement (git tag); restore from tag |

Every migration file must document an application rollback and any schema rollback separately, and be rehearsed on a scratch restore. A schema rollback that drops populated objects is not presumed safe. Restoring a backup is a last resort and requires explicit operator direction; no destructive rollback runs against a live database.

## 9. KPI and alert regression testing

### 9.1 Why “same database before/after”
The seed is anchored to `now()` at load and its values follow a sine of the epoch (`db/init/anzma_schema_and_seed.sql`), so two rebuilds produce different numbers (handover limitation 2). Regression must therefore compare **the same database instance** (restored copy) **before and after** a change — never two freshly seeded databases.

### 9.2 Golden snapshot (captured at M0, re-run at every gate)
Against a restored scratch copy with the app pointed at it:

| Area | Capture | Compare |
|---|---|---|
| KPI | `GET /dashboard/summary` for: no filter; each `system_type`; `building_id=1`, `2`; a fixed `start_time/end_time` | exact JSON equality (values, units, status_counts, heartbeat received/expected) |
| Dashboard page | `/dashboard/overview?hours=24`, `/dashboard/trends?hours=24`, `/dashboard/equipment-health?hours=24`, `/alerts`, `/alerts/{id}` for every alert | exact equality of fields (excluding nothing; they are data-relative, not wall-clock) |
| Alerts | set of `(rule_id, asset_id, first_ts, last_ts, severity, detection_method, title, summary)` and `evidence` JSON per alert | identical set and identical evidence |
| Detection | run `pipeline.detect()` twice on the migrated copy | `new_alerts = 0` (idempotent; nothing new unless a change was approved) |
| Incidents | `(incident_id, alert_ids, severity, status, first_ts, last_ts, title)` and ranking from `incident_engine.list_incidents` | identical |
| Evidence | `evidence.alert_evidence(alert_id)` for every alert: `total`, `verified`, `unverified` | identical; `unverified = 0` |
| AI/recommendations | `pipeline.explain_alert(..., use_llm=False, persist=False)` for every alert: `validation.status`, `data_sufficiency`, `recommendations[].action_id`, `probable_causes[].id` | identical (deterministic path) |
| Chat | `test_chat_consistency.py` (31 checks) and the deterministic chat intents | all pass |
| Test suites | `test_day2_ahmed.py`, `test_day2_integration.py`, `test_frontend_api.py` | all pass |
| Integrity | `integrity_rules.stored_violations(cur)` | `{'telemetry': 0, 'events': 0}` |

### 9.3 New tests to add (Sprint 1 scope)
Contract validation (accept/reject matrix), idempotent replay (same batch twice → one set of rows), duplicate-vs-conflict behaviour, late/out-of-order data, freshness classification boundaries (`FRESH/STALE/MISSING`), “missing telemetry never sets availability offline”, per-building scoping, Camera-2 scenario (KPI `no_data` unchanged; `freshness=MISSING` added), `alert_evidence` parity with JSON.

### 9.4 Changes that would alter existing results (each needs explicit approval and a re-baseline limited to its diff)

| Change | Expected result difference | Protection |
|---|---|---|
| Fix `bandwidth_util` unit/thresholds (F-05, **Q1**) | Implemented and regression verified: rules/facts use `%`; `NET_BANDWIDTH_HIGH` / `NET_BANDWIDTH_ANOMALY` can fire; `NET_LOSS` can select `CONGESTION`; seed remains below the provisional 80% warning threshold | Verified 138/138 unit/integration checks and 14/14 fallback + 15/15 live S1 checks. Thresholds remain provisional until capacity data is recorded. |
| Per-building timezone replacing fixed UTC+2 (F-11, **Q2**) | “after hours” facts differ only if the IANA zone’s offset ≠ +2 at the event time (e.g. during DST) | Backfill the zone that reproduces +2 where intended, or accept and document differences; diff limited to `after_hours`-dependent rules |
| `zone_type` replaces `'Server Room'` match (F-12) | None if the existing room is flagged | Parity test |
| Reconcile `v_asset_overview` with KPI health (F-07) | Chat answers using that view may change where a recovered paired fault is counted | Compare both definitions on current data first; approve which is canonical |
| `quality` filter (BAD excluded) | None on existing data (all `GOOD`) | Test with injected `BAD` row on a copy |
| Unique index on `alerts` (F-14) | None unless duplicates exist | Pre-check query (§4) |
| Moving KPI “Alerts raised” to `alerts` (F-10, **Q6**) | Different definition | **Not planned**; KPI keeps counting events |

## 10. Validation and reconciliation procedures

Run before M1, after each phase, and after any restore.

1. **Row counts** per table before/after (must be equal for existing tables; new tables as expected).
2. **Content hashes** over immutable columns, e.g.
   `SELECT md5(string_agg(format('%s|%s|%s|%s|%s',telemetry_id,asset_id,metric_id,ts,value), ',' ORDER BY telemetry_id)) FROM telemetry_records;`
   (analogous for `event_records`, `alerts` key columns incl. `evidence::text`, `incidents`, and `alerts(alert_id, incident_id)`). Hash equality proves no historical record changed.
3. **Alert→incident integrity:** every `alerts.incident_id` resolves; incident `first_ts/last_ts/severity` equal before/after; `count(alerts) per incident` equal.
4. **Evidence integrity:** for every alert `evidence.alert_evidence()` shows `unverified = 0`; `alert_evidence` row count per alert equals `jsonb_array_length(alerts.evidence->'events')`; each `alert_evidence` row resolves to an existing fact row with equal `ts`/`value`.
5. **Building consistency:** no row where `building_id` differs from the building derived through `assets → locations`.
6. **Orphans:** no FK violations (`VALIDATE CONSTRAINT` succeeds); `alerts` without asset = 0.
7. **Provenance:** every row has a `source_id` after backfill; legacy rows = `legacy-seed`; `received_at IS NULL` only for legacy rows.
8. **Compatibility violations:** `integrity_rules.stored_violations()` equals the baseline.
9. **Golden snapshot diff** (§9.2) empty except approved changes.
10. **Restore drill:** restore dump into a scratch DB; steps 1–9 give identical results.

Report format: one table per run (check, before, after, status), kept with the phase record.

## 11. Deployment considerations

- **Fresh vs existing databases:** Compose mounts migrations 003–007 into `docker-entrypoint-initdb.d`, which Postgres runs **only on an empty volume**. Existing deployments use the advisory-locked runner for 006+; changing init mounts alone does not upgrade them.
- **Fresh installs** should eventually be created by the same runner from a baseline snapshot, so fresh and migrated databases converge.
- **Order of deployment:** deploy code that tolerates the old schema → back up/review → apply migrations 006–007 → provision building/source credentials. The v1 route returns a schema-not-applied error before migration.
- **Zero-downtime considerations:** additive DDL is metadata-only; backfill in small batches; `CREATE INDEX CONCURRENTLY`; run validation off-peak.
- **Detection loop** runs inside the API process (`app/main.py`); consider an advisory lock if multiple workers or concurrency tests show a race remains.
- **Secrets:** no credentials in migrations or docs; `.env` is untracked and must not be shipped in archives (`README.md` and `.gitignore` confirm it is git-ignored; the hand-over ZIP contained one).
- **Network exposure:** the DB port (5432) and API are published and unauthenticated (`README.md` limitations); add authentication before connectors send real data.

## 12. Risks and dependencies

| # | Risk / dependency | Mitigation |
|---|---|---|
| 1 | Existing Compose initialization only runs for an empty volume; existing deployments need the runner | Migration runner is implemented and rehearsed on a disposable pre-006 database; operator must still target/approve deployment. |
| 2 | Result-changing items (§9.4) need business decisions (Q1, Q2, Q6) | Decide before the affected phase; keep behind flags |
| 3 | Backfill on large tables | Batched, resumable, off-peak; measure on a copy |
| 4 | `building_id` composite FKs require UNIQUE targets and a validated backfill | `NOT VALID` → `VALIDATE` pattern |
| 5 | Legacy `received_at` unknown | Keep NULL; documents and APIs must treat NULL as “unknown” |
| 6 | Two DB drivers and three connection factories complicate shared code | Consolidate in M6; new code uses the driver already used by the module it extends |
| 7 | Detection loop in API process duplicates under multiple workers | Advisory lock before scaling |
| 8 | Test suites rebuild databases from the base schema | Apply migrations 003–007 in the test harness; tested in isolated databases. |
| 9 | Seed is data-relative and non-reproducible across loads | Same-database before/after comparison (§9.1) |
| 10 | Existing-database migration is an operational deployment step | Use the implemented advisory-locked runner after backup/review; no live migration performed here. |
| 11 | Legacy API/DB surfaces do not have broad authentication | v1 ingestion is protected by source-scoped keys; expanding auth beyond approved ingestion is separate scope. |
