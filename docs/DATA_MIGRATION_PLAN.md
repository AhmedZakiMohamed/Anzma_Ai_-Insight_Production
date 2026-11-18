# DATA_MIGRATION_PLAN.md — Anzma AI Insight Core

| | |
|---|---|
| **Status** | Plan only. **No migration is written or applied in Day 1.** No SQL here is to be executed as-is. |
| **Principle** | Incremental, additive, reversible. Existing tables, columns, views and API shapes keep working at every step. |
| **Related** | `CORE_DATA_ARCHITECTURE.md`, `CANONICAL_DATA_MODEL.md`, `HISTORIAN_DESIGN.md`, `SPRINT_1_TECHNICAL_ISSUES.md` |

---

## 1. Current-to-target mapping

Format: **Current structure → Proposed canonical structure → Required adaptation.** “None” means existing code keeps working unchanged.

### 1.1 Tables and columns

| Current (verified) | Proposed canonical | Required adaptation |
|---|---|---|
| `buildings(building_id, name)` | + `organization_id`, `building_code`, `timezone`, `work_start_local`, `work_end_local` | None for readers. `alert_config.LOCAL_UTC_OFFSET/WORK_START/WORK_END` later read from the building (Phase M5) |
| *(none)* | `organizations` | One default row; backfill `buildings.organization_id` |
| `locations(location_id, building_id, floor, name)` | + `parent_location_id`, `location_type`, `zone_type`, `is_sensitive` | None. `'Server Room'` string checks (`alert_engine.py`, `chat_lite.py`) later read `zone_type` |
| `system_types(code CHECK …)` | same codes; CHECK dropped | None. `reference_data.SYSTEM_CODES` later read from the table |
| `assets(asset_id, asset_code, name, asset_type, system_type_id, location_id)` | + `building_id`, `parent_asset_id`, `manufacturer`, `model`, `serial_number`, `lifecycle_status`, `installed_at`, `metadata` | None. `building_id` backfilled from `locations` |
| *(none)* | `asset_external_ids`, `data_sources` | New; seed `legacy-seed`, `core-engine` |
| `metric_types(metric_id, name, unit)` | + `value_kind`, `min_plausible`, `max_plausible`; `unit` = canonical unit | `bandwidth_util` remains canonical `%` in the current runtime; thresholds are provisional until per-link capacity data exists. |
| `event_types(event_type_id, name)` | + `event_class`, `resolves_event_type_id` | Later replaces `FAULT_RECOVERY_PAIRS`, `CLOSERS`, `v_camera_status` pair logic (Phase M6, parity-tested) |
| `metric_type_system_compat`, `event_type_system_compat` | unchanged; enforced by the ingestion service | `integrity_rules.EVENT_CATALOG` (Python duplicate) retired later |
| `telemetry_records(telemetry_id, asset_id, metric_id, ts, value)` | + `building_id`, `received_at`, `source_id`, `batch_id`, `quality`, `contract_version`, `source_metadata`; `ts` = observed time | None. Legacy rows: `source_id`→`legacy-seed`, `quality='GOOD'`, `received_at` **NULL** (unknown, not guessed) |
| `event_records(event_id, asset_id, event_type_id, ts, severity, details)` | + `building_id`, `received_at`, `source_id`, `source_event_id`, `contract_version` | None |
| `alerts(…, evidence JSONB, incident_id)` | + `building_id`, `detected_at`, `acknowledged_ts/by`, `resolved_by`, `rule_version`; UNIQUE `(rule_id, asset_id, first_ts)` | `db_adapter.store_alerts`: `NOT EXISTS` → `ON CONFLICT DO NOTHING` (same semantics) |
| `alerts.evidence` JSON (`ref`, `source`, `record_id`, …) | `alert_evidence` rows (dual-write) | `evidence.py` keeps reading JSON until parity is proven |
| `incidents(location_id, …)` | + `building_id` | None |
| *(none)* | `asset_state_current`, `asset_state_history`, `reporting_expectations`, `data_gaps`, `ingestion_batches`, `ingestion_rejections`, `retention_policies`, `unit_conversions` | New; read by new code only until M5 |
| `scenario_runs` | later moved to schema `eval` | None; no code references it |
| Views `v_data_clock`, `v_asset_overview`, `v_incident_overview`, `v_alert_overview`, `v_camera_status` | same names/columns + `building_id` | Chat keeps working (`chat_lite.py` lists these views in its prompt). `v_asset_overview` health rule reconciled with the KPI in M6 (§9.4) |

### 1.2 Consumers and the adaptation they need

| Consumer | Evidence | Compatibility assessment |
|---|---|---|
| KPI engine `app/dashboard_core_pg.py` | SQL on `assets, system_types, locations, event_records, event_types, telemetry_records, metric_types`; constants `temperature`, `packet_loss`, `bandwidth_util`, `heartbeat` | **Compatible without change** through M1–M4 (no column removed/renamed; added columns are ignored). Time windows `[start, end)`; building filter via `locations.building_id` stays valid |
| Alert engine `day2_ahmed/alert_engine.py` + `db_adapter.load_context` | reads `assets`, `event_records` (via `EVENT_MAP`), `telemetry_records` (`temperature`, `bandwidth_util`, `packet_loss`) | **Compatible.** Remaining adaptations: timezone (F-11), zone_type (F-12). The current runtime aligns bandwidth thresholds/facts to `%`. A `quality` filter is added to the loaders and is a no-op on existing data |
| Incident engine `incident_engine.py` | `alerts`, `incidents`, `assets`, `locations`; grouping rule `same_asset_or_system_within_15_min` | **Compatible.** `building_id` is populated, grouping unchanged |
| Evidence `evidence.py` | `alerts.evidence` JSON; re-reads `event_records.event_id`, `telemetry_records.telemetry_id`; compares `ts`, `value`, event type through `db_adapter.EVENT_MAP` | **Compatible.** Ids and values are never changed; `alert_evidence` is added in parallel |
| Validation / AI `validation.py`, `ai_explainer.py`, `recommendations.py` | `alerts` row, `alert_config.PLAYBOOKS` | **Compatible**, no data-model dependency beyond `alerts` |
| Chat `app/chat_facts.py`, `app/chat_lite.py` | `v_*` views, KPI functions, event/telemetry SQL, read-only SQL tool | **Compatible** if view names/columns are preserved; staged local edits are **not touched** by this plan |
| Dashboard `app/frontend_api.py`, `static/dashboard.html` | inline SQL on the fact/reference tables; response shapes | **Compatible**; shapes unchanged |
| Tests | `test_day2_ahmed.py` (25 offline), `test_day2_integration.py` (43, rebuilds DB from schema + 003), `test_frontend_api.py`, `test_chat_consistency.py` (31 checks, live API) | Must pass unchanged at every phase |

---

## 2. Existing structures that will be retained

All of: the 13 tables and 5 views; table/column names; enum value sets (`'open','acknowledged','resolved'`, `'info','warning','critical'`, detection methods); UNIQUE keys; `db_adapter` translation layer (`SYSTEM_TO_API`, `enum_to_api/db`); `alerts.evidence` JSON; incident lifecycle and grouping logic; `POST /events`, `POST /alerts`, `/alerts/run`, `/incidents*`, `/dashboard/*`, `/chat`, `/ask`; the seed file as the base for fresh databases; Ollama + deterministic fallback; playbooks.

## 3. Proposed structures that may be added

`organizations`, `data_sources`, `asset_external_ids`, `ingestion_batches`, `ingestion_rejections`, `reporting_expectations`, `asset_state_current`, `asset_state_history`, `data_gaps`, `alert_evidence`, `retention_policies`, `unit_conversions` (optional), `schema_migrations`; views `v_telemetry_valid`, `v_telemetry_canonical`, `v_event_canonical`, `v_series_freshness`; later (measured need only) `telemetry_rollup_1h/1d`. Definitions: `CANONICAL_DATA_MODEL.md`.

## 4. Schema changes required (by migration unit)

Numbering continues after the existing `004_chat_views.sql`. Names are proposals; each file idempotent (`IF NOT EXISTS`) like `003`.

| Unit | Contents | Locking / risk |
|---|---|---|
| `005_schema_migrations_and_orgs.sql` | `schema_migrations`; `organizations` + default row; `buildings` new columns (nullable) | Metadata-only |
| `006_sources_and_assets.sql` | `data_sources` (+ rows `legacy-seed`, `core-engine`); `asset_external_ids`; `assets`, `locations` new columns; drop `system_types` CHECK (look up the real constraint name in `pg_constraint` first) | Metadata-only except CHECK drop (brief lock) |
| `007_fact_columns.sql` | `telemetry_records`, `event_records`, `alerts`, `incidents` new columns, **nullable, no default** | `ADD COLUMN` without default is metadata-only |
| `008_backfill.sql` (or script) | Batched `UPDATE` of `building_id`, `source_id`, `quality`; `alert_evidence` from JSON | Batched by id range; long-running on large tables; **only new columns are written** |
| `009_constraints.sql` | `NOT VALID` FKs/CHECKs then `VALIDATE CONSTRAINT`; `SET DEFAULT` for `received_at` (`now()`) and `quality` (`'GOOD'`); partial/unique indexes `CREATE INDEX CONCURRENTLY` | See pitfall below |
| `010_state_and_expectations.sql` | `asset_state_*`, `reporting_expectations` (+ initial rows from observed 900 s / 3600 s), `data_gaps` | New tables |
| `011_views.sql` | compatibility and building-aware views (`CREATE OR REPLACE`) | None |
| `012_ingestion_tables.sql` | `ingestion_batches`, `ingestion_rejections`, `retention_policies` (disabled) | New tables |

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
8. **Local changes protection:** `app/chat_facts.py`, `app/chat_lite.py`, `docker-compose.yml`, `004_chat_views.sql` are owned by their authors; migrations 005+ never edit them. Because `docker-compose.yml` is staged and `004` is untracked, `004` must be committed with the compose change by its owner so a fresh checkout still initialises.

## 6. Migration phases

| Phase | Goal | Changes behaviour? |
|---|---|---|
| **M0 Baseline** | Backup + golden snapshot (§9) on a scratch copy; restore rehearsal | No |
| **M1 Foundation (005–007)** | Org/building/location/asset/source structure; fact columns (nullable) | No |
| **M2 Backfill (008)** | `building_id`, `source_id`, `quality`, `alert_evidence`; `received_at` stays NULL | No |
| **M3 Constraints & views (009, 011)** | Validate FKs; defaults for new rows; compat views | No (new rows only) |
| **M4 Ingestion (012 + new code)** | Canonical contract endpoint behind a flag; `POST /events` unchanged | No for existing flows |
| **M5 State & freshness (010 + job)** | Shadow-compute state/freshness; expose alongside existing KPI; per-building timezone/zone_type/expectations introduced behind flags | Only where a flag is turned on and approved |
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
| M1 | Drop added columns/tables (all additive, nothing depends on them yet); restore from dump if in doubt |
| M2 | Set new columns back to NULL / drop them; originals untouched |
| M3 | Drop new constraints/views (`DROP CONSTRAINT`, `CREATE OR REPLACE` previous view text kept in version control) |
| M4 | Turn the feature flag off; ingested canonical rows are valid facts and remain (source ≠ `legacy-seed`) — or are excluded via `source_id` if a source must be withdrawn |
| M5 | Turn flags off; new state tables ignored by legacy code |
| M6 | Release-by-release: keep legacy code one release after replacement (git tag); restore from tag |

Every migration file ships with a documented down-procedure and is rehearsed on the scratch restore. Rollback never requires restoring fact data because facts are not modified. A full restore from the pre-phase dump is the last resort.

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

- **Fresh vs existing databases:** `docker-compose.yml` mounts the schema, `003` and `004` into `docker-entrypoint-initdb.d`, which Postgres runs **only on an empty volume**. Existing deployments are not upgraded by editing those mounts. A migration runner (apply files `005+` in order, record in `schema_migrations`, advisory-locked) is needed — as a one-shot command or one-shot compose service; its exact form is for Amr to decide. This assignment did not change any Docker configuration.
- **Fresh installs** should eventually be created by the same runner from a baseline snapshot, so fresh and migrated databases converge.
- **Order of deployment:** migrate database → deploy code that tolerates both old and new schema → enable flags.
- **Zero-downtime considerations:** additive DDL is metadata-only; backfill in small batches; `CREATE INDEX CONCURRENTLY`; run validation off-peak.
- **Detection loop** runs inside the API process (`App.py`); apply the advisory-lock change before running more than one API worker.
- **Secrets:** no credentials in migrations or docs; `.env` is untracked and must not be shipped in archives (`README.md` and `.gitignore` confirm it is git-ignored; the hand-over ZIP contained one).
- **Network exposure:** the DB port (5432) and API are published and unauthenticated (`README.md` limitations); add authentication before connectors send real data.

## 12. Risks and dependencies

| # | Risk / dependency | Mitigation |
|---|---|---|
| 1 | Local uncommitted work (3 staged files + untracked `004`) could be lost or conflict | Coordinate with owners; do not edit those files in migrations; commit order agreed first |
| 2 | Result-changing items (§9.4) need business decisions (Q1, Q2, Q6) | Decide before the affected phase; keep behind flags |
| 3 | Backfill on large tables | Batched, resumable, off-peak; measure on a copy |
| 4 | `building_id` composite FKs require UNIQUE targets and a validated backfill | `NOT VALID` → `VALIDATE` pattern |
| 5 | Legacy `received_at` unknown | Keep NULL; documents and APIs must treat NULL as “unknown” |
| 6 | Two DB drivers and three connection factories complicate shared code | Consolidate in M6; new code uses the driver already used by the module it extends |
| 7 | Detection loop in API process duplicates under multiple workers | Advisory lock before scaling |
| 8 | Test suites depend on schema file + `003` (`test_day2_integration.py` rebuilds the DB) | Keep base files loadable; apply `005+` in the test harness |
| 9 | Seed is data-relative and non-reproducible across loads | Same-database before/after comparison (§9.1) |
| 10 | Deployment mechanism (runner, compose) is outside this assignment’s scope | Amr to decide; this plan lists requirements only |
| 11 | No authentication on API/DB | Required before real connectors; scope decision (**[Open]**) |
