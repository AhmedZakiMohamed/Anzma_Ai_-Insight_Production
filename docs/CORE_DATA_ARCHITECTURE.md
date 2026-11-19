# CORE_DATA_ARCHITECTURE.md — Anzma AI Insight Core

| | |
|---|---|
| **Document** | Week 3 / Day 1 — Data Architecture & Core Reliability |
| **Status** | Historical runtime baseline plus implemented ingestion extensions in migrations 006–007. Other target architecture sections remain proposals unless marked implemented. |
| **Source of truth** | Repository code/migrations on `feature/ingestion-foundation`, plus the separately recorded read-only inspection of the pre-006 running database. Migration 006 has not been applied to that existing database. |
| **Related** | `CANONICAL_DATA_MODEL.md`, `HISTORIAN_DESIGN.md`, `DATA_MIGRATION_PLAN.md`, `SPRINT_1_TECHNICAL_ISSUES.md` |

Conventions used in all five documents:

- **[Verified]** — read directly in code/SQL in the archive (file path given).
- **[Proposed]** — design recommendation; does not exist today.
- **[Assumption]** / **[Open]** — needs confirmation (see section 13).
- Earlier archive findings are historical where noted. Runtime table and column claims in this revision were checked with read-only `information_schema` queries; the offline unit suite passed 25/25 on this checkout.

> **Current database authority:** this Day 1 review is a historical analysis. For the agreed runtime schema and migration order, use [DATABASE_BASELINE.md](DATABASE_BASELINE.md). The working tree keeps the bootstrap schema at `db/init/` and versioned migrations at `db/migrations/`.

---

## 1. Executive summary

Anzma AI Insight today is a single-database, simulated-data application. The historical deployed schema has 13 tables and 5 views; new installs add seven tables and two views through migration 006, then source-aware event uniqueness through migration 007. The load order is:

1. `db/init/anzma_schema_and_seed.sql` — 12 tables + seed.
2. `db/migrations/003_incidents.sql` — `incidents` table and `alerts.incident_id`.
3. `db/migrations/004_chat_views.sql` — 5 read-only views (`v_data_clock`, `v_asset_overview`, `v_incident_overview`, `v_alert_overview`, `v_camera_status`).
4. `db/migrations/005_alert_uniqueness.sql` — unique alert key used by `store_alerts`.
5. `db/migrations/006_ingestion_foundation.sql` — sources/hashed credentials, scoped asset mappings, nullable fact provenance and quality, batch/rejection audit, state facts, and compatibility views.
6. `db/migrations/007_event_source_identity.sql` — preserve the legacy event key only for rows without source IDs; source events are unique by `(source_id, source_event_id)`.

The existing intelligence (KPI engine, alert engine, incident engine, evidence verification, grounding validation, chat assistant) is coherent and tested (`TEST_RESULTS_DAY2.txt`: 43/43 integration checks; `TEST_RESULTS_CHAT_CONSISTENCY.txt`: 31/31). **It should be preserved, not rewritten.**

What stands between this code base and a reusable, vendor-independent Core:

| # | Gap | Evidence (short) |
|---|---|---|
| 1 | **Ingestion APIs:** legacy `POST /events` and `POST /alerts` remain; versioned `POST /api/v1/ingest/batches` accepts canonical telemetry/event/state records. | `app/main.py`; `app/services/ingestion.py` |
| 2 | **No building identity on operational records.** `telemetry_records`, `event_records`, `alerts`, `incidents` carry no `building_id`; building is derived by joining through `assets → locations`. No customer/organization concept. | schema SQL |
| 3 | Migration 006 adds nullable observed/received provenance, source and quality fields; historical rows retain NULL for unknown metadata. | migration 006 |
| 4 | **Equipment state and data freshness are conflated.** `no_data` is a member of the health-status enum. “Now” is the latest stored record, so a dead feed looks current. | `dashboard_core_pg.py`, `004_chat_views.sql` |
| 5 | **Bandwidth thresholds are provisional.** The runtime stores utilisation as `%`; the current working tree aligns rules and labels to `%` but has no per-link capacity data for calibration. | `alert_config.py`, seed |
| 6 | **Hardcoded local assumptions**: UTC+2 offset, 08–18 working hours, zone name `'Server Room'`. | `alert_config.py`, `alert_engine.py`, `chat_lite.py` |
| 7 | **Three competing “authoritative” data-model documents** (22-table draft in `data-model.sql`/`DATA_MODEL.md`/`ERD-new.png`, proposed `/api/v1` in `docs/`, and the real 13-table runtime). | see §5 |
| 8 | **Duplicated business logic** for asset health (4 implementations) and fault/recovery pairs (3). | see §5 |

**Proposed direction (simplest reliable path):** keep PostgreSQL and the existing table names; evolve them **additively** (nullable columns, new small tables, compatibility views); introduce a canonical JSON contract at the Core boundary; add a thin ingestion service and a state/freshness layer; then consolidate duplicated SQL behind one repository layer with parity tests. No new infrastructure (no TimescaleDB, Kafka, Redis).

---

## 2. Existing architecture overview

```
Anzma-Platform-Clean/
├── app/main.py                  FastAPI app + Socket.IO; background detection loop (asyncio)
├── app/api/                     dashboard, chat and frontend route modules
├── app/domain/                  alert, incident, evidence, operations and AI rules
├── app/services/pipeline.py     detection, evidence-grounded explanation workflows
├── app/adapters/database/       shared DB operations and compatibility mapping
├── app/repositories/            dashboard KPI queries
├── db/init/anzma_schema_and_seed.sql      authoritative schema + seed (docker init)
├── static/                      dashboard.html, chat widget
├── docker-compose.yml           db (postgres:16), ollama, ollama-init, api
└── legacy/                      chat_engine.py, chat_api.py, dashboard_api.py (old schema, unused),
    data-model.sql, DATA_MODEL.md, ERD-new.png, docs/ARCHITECTURE.md, docs/API_CONTRACT.md
```

---

## 3. Runtime data flow

![runtime data flow](./img/current_runtime_data_flow.png)

Key behaviours [Verified]:

- **Fact writes:** seed SQL and legacy `POST /events` remain; authenticated connector batches write telemetry, events and state through `POST /api/v1/ingest/batches`. The legacy event route retains its compatibility validation and duplicate behavior.
- **“Now” is data-relative.** `pipeline.detect()` uses `db_adapter.latest_ts()` = `GREATEST(max telemetry ts, max event ts)`; KPI windows (`frontend_api._window`) and `v_data_clock` do the same. Documented as intentional (`DAY2_AHMED_HANDOVER.md`, limitation 8).
- **Detection runs inside the API process** (`app/main.py`, `_alert_loop`, every `DETECT_INTERVAL` seconds, default 60) and on demand via `POST /alerts/run`. `db_adapter.store_alerts()` uses application-level duplicate handling and migration `005_alert_uniqueness.sql` adds the matching database unique index; concurrent-run locking remains a separate issue.
- **Incident lifecycle** is driven only through `POST /incidents/{id}/status` (`incident_engine.set_status`: open → acknowledged → resolved, resolved is final). Status changes cascade to the incident's alerts. No endpoint changes a single alert's status.
- **AI**: Ollama phrases explanations; the deterministic fallback is built from the same stored evidence; every final text is grounding-checked (`validation.py`). Recommendations come only from approved playbooks (`alert_config.PLAYBOOKS`), never from the model.

---

## 4. Pre-006 database architecture

### 4.1 Tables (authoritative runtime) [Verified: `db/init/anzma_schema_and_seed.sql`, `003_incidents.sql`]

| Table | Key columns / constraints | Notes |
|---|---|---|
| `buildings` | `building_id` PK; `name` UNIQUE | Seed: Main Tower (1), Data Center Annex (2) |
| `locations` | `location_id` PK; `building_id` FK; `floor`; `name`; UNIQUE `(building_id, name)` | Used as “zone” everywhere |
| `system_types` | `system_type_id` PK; `code` UNIQUE with `CHECK code IN ('HVAC','NETWORKING','ACCESS_CONTROL','CCTV','FIRE_ALARM')` | Adding a system requires altering the CHECK |
| `assets` | `asset_id` PK; `asset_code` UNIQUE; `name`; `asset_type` (free text); `system_type_id` FK; `location_id` FK | No manufacturer/model/serial/external id/parent/status columns |
| `metric_types` | `metric_id` PK; `name` UNIQUE; `unit` | 8 metrics (temperature °C as `'C'`, humidity, power_kw, cpu_load, packet_loss, bandwidth_util `'%'`, smoke_level, camera_fps) |
| `event_types` | `event_type_id` PK; `name` UNIQUE | 12 types incl. `heartbeat` |
| `metric_type_system_compat`, `event_type_system_compat` | composite PK, FKs | Which metric/event is valid for which system. **Not enforced on INSERT** (comment in schema file and `integrity_rules.py`) |
| `telemetry_records` | `telemetry_id` PK; `asset_id`, `metric_id` FKs; `ts TIMESTAMPTZ`; `value DOUBLE PRECISION` (finite CHECK); UNIQUE `(asset_id, metric_id, ts)` | No source, quality, received time, building |
| `event_records` | `event_id` PK; `asset_id`, `event_type_id` FKs; `ts`; `severity` (info/warning/critical); `details JSONB`; UNIQUE `(asset_id, event_type_id, ts)` | Device-reported severity; heartbeats stored here |
| `alerts` | `alert_id` PK; `rule_id`; `detection_method` (threshold/anomaly/compound); `severity`; `asset_id` FK; `first_ts`,`last_ts`,`detected_ts`; `evidence JSONB NOT NULL`; `status` (open/acknowledged/resolved); `resolved_ts`; `explanation JSONB`; `explanation_source` (ollama/fallback); `incident_id` FK (003) | CHECKs: `last_ts >= first_ts`; resolved ⇔ `resolved_ts` set |
| `incidents` (003) | `incident_id` PK; `location_id` FK; `title`; `severity`; `status`; `grouping_rule`; `first_ts`,`last_ts`; `acknowledged_ts`,`resolved_ts` | Same CHECKs as alerts |
| `scenario_runs` | arrays of ids (no FKs possible) | Test harness in the `public` schema. No reference found in any `.py` file |

All timestamps are `TIMESTAMPTZ`. Indexes: `idx_assets_location`, `idx_assets_system_type`, `idx_telemetry_ts`, `idx_event_ts`, `idx_event_asset_ts`, `idx_alerts_first_ts`, `idx_alerts_asset_ts`, `idx_alerts_severity`, `idx_alerts_open` (partial), `idx_alerts_incident`, `idx_incidents_location_last`, `idx_incidents_status`, plus the UNIQUE-backed indexes.

### 4.2 Views (004, untracked) [Verified]

`v_data_clock`, `v_asset_overview` (24 h health per asset), `v_incident_overview`, `v_alert_overview`, `v_camera_status`. These exist for the chat assistant (`chat_lite.py` lists them in its system prompt) and are the first example of a **compatibility/read layer** — the pattern this proposal extends.

### 4.3 Seed characteristics relevant to design

The seed (`generate_series` every 15 minutes for 78 h, hourly heartbeats, 15 injected events) is anchored to `now()` at load (`seed_anchor`). Values follow a sine curve of the epoch, so **absolute values and warning-vs-critical peaks differ between loads** (handover limitation 2). Consequence for testing: regression comparisons must be made on **the same database before and after a change**, not across rebuilds (see `DATA_MIGRATION_PLAN.md` §9).

---

## 5. Identified inconsistencies and dependencies

Priority: P0 critical reliability/integrity · P1 required for productization foundation · P2 important · P3 future.
“Impact” = potential effect on existing functionality if fixed.

### 5.1 Findings table

| ID | File / object | Current behaviour | Problem | Recommendation | Pri | Impact if changed |
|---|---|---|---|---|---|---|
| F-01 | `app/main.py`, `app/services/ingestion.py` | Versioned batch route ingests canonical telemetry/event/state; existing `POST /events` and `POST /alerts` remain. | New route requires migration 006 and a provisioned source/key. | Implemented; route contract and operational limits are in `INGESTION_CONTRACT.md`. | Complete | Legacy endpoints unchanged |
| F-02 | `db/migrations/006_ingestion_foundation.sql` | Adds nullable `source_id`, `received_at`, `quality`, batch/version/metadata to facts; old records remain NULL. | Building still derives through assets/locations on facts; no per-fact building column was added. | Implemented as additive provenance; no historical backfill. | Complete | None |
| F-03 | `alerts`, `incidents`; `dashboard_core_pg.SCOPE_CTE`; `app/api/frontend_api.py`; `004_chat_views.sql` | KPI summary accepts `building_id` through `dashboard_core_pg.SCOPE_CTE`; the dashboard overview/trends/equipment-health and alert/incident/chat query paths do not consistently accept an equivalent building scope. Facts derive building through assets/locations; no organization table exists. | The schema supports multiple buildings, but query scoping is incomplete; cross-building isolation is not provided. | Add consistent optional building scope through repositories and views while preserving the current all-building default and API response shapes. Organization isolation remains a separate deployment decision. | P1 | Existing default results stay all-building |
| F-04 | `db_adapter.SYSTEM_TO_API`, `frontend_api.SYSTEM_TO_UI`/`SYSTEM_FROM_UI`, `reference_data.SYSTEM_CODES` | DB: `NETWORKING`, `ACCESS_CONTROL`, lower-case enums; engine/API: `NETWORK`, `ACCESS`, upper-case; `locations.name` ⇒ “zone”. Translation maps duplicated in two modules (+ `chat_facts.SYSTEM_WORDS`). | Three vocabularies; drift risk. | Canonical codes = DB codes; keep one translation module at the API edge. | P2 | Presentation only |
| F-05 | `alert_config.T`, `db_adapter.METRIC_MAP`, `integrity_rules.EVENT_CATALOG`, seed `metric_types` | `bandwidth_util` is `%` in the DB. Rules, facts, summaries, anomaly labels, and playbook comparisons use percent. Provisional thresholds: warning 80%, critical mean 90%, anomaly minimum deviation 15 percentage points. | Unit and integration regressions pass 138/138; seeded data remains 10 alerts / 8 incidents with no bandwidth-high/anomaly alert (maximum 72.32%, no sustained 15-point deviation); synthetic 85% packet-loss case selects congestion. No per-link capacity data exists to calibrate the thresholds. | Keep percent canonical; calibrate limits when per-link capacity is available. | P0 | **Changes results** (network alerts and congestion explanations may change); regression baseline required |
| F-06 | `alert_engine.py` (`rule_hvac_filter`, `rule_cctv`, `rule_fire`), `db_adapter.EVENT_MAP` | Rules read event types `FILTER_FAULT`, `MOTION_DETECTED`, `BATTERY_LOW`, `PANEL_HEARTBEAT`, `ACCESS_GRANTED`, and detail keys `fan_on`, `mains_ok`, `battery_pct`. `EVENT_MAP` maps only 9 of the 12 seeded event types; none of those names exist in seed `event_types`. | `HVAC_FILTER_FAULT`, `CCTV_AFTER_HOURS_MOTION`, `FIRE_BATTERY_LOW` and several playbook causes (`FAN_FAILURE`, `FILTER_BLOCKAGE`, `MAINS_LOSS`) cannot trigger on current data. `SEC_BLIND_SPOT_ACCESS` needs a camera and reader in the same location while the camera is offline; the seed has no such scenario. | Not a bug to “fix” now: document as dormant; make event/metric catalogue data-driven so connectors can supply them. | P2 | None |
| F-07 | `dashboard_core_pg._asset_status_counts`, `frontend_api.equipment_health` (inline SQL), `004_chat_views.sql` `v_asset_overview`, `chat_facts._conditions/_status` | Asset health is implemented four times. `v_asset_overview` uses a 24 h window `(as_of-24h, as_of]` and counts **any** critical event as `critical`; the KPI logic excludes recovered paired faults (`FAULT_RECOVERY_PAIRS`) and uses `[start, end)`. | Same word (“critical”) can differ by code path; consistency today depends on the test suite (`test_chat_consistency.py`, 31 checks) and on the chat using KPI functions for status. (By code reading; not executed.) | One health definition in one repository/view, parity-tested against current outputs. | P2 | Possible small number changes in chat answers that use `v_asset_overview`; must be reconciled explicitly |
| F-08 | `dashboard_core_pg.FAULT_RECOVERY_PAIRS`, `integrity_rules.CLOSERS`, `v_camera_status` | Opener/closer pairs defined three times; `hvac_fault`/`hvac_recovered` appear in the KPI pairs but are unmapped in the alert engine. | Duplicated rule data. | Put `resolves_event_type_id` and `event_class` on `event_types` (idea already present in the repo’s draft `data-model.sql`). | P2 | None if parity-tested |
| F-09 | `integrity_rules.EVENT_CATALOG` vs `event_type_system_compat` / `metric_type_system_compat` | The same “what is valid for which system” knowledge is in Python and in SQL. DB does not enforce it on INSERT. | Duplicated and unenforced. | Single source = DB tables (+ validation in the ingestion service). | P2 | None |
| F-10 | `dashboard_core_pg` KPIs `alerts_raised`, `critical_alerts`, `alert_rate_per_hour` | These count **`event_records`** with severity warning/critical, not rows of `alerts`. The UI labels them “Alerts raised”. | Two meanings of “alert” (device event with severity vs. engine-detected alert). Conflicts with the Event / Alert / Incident distinction required for the product. | Keep KPI values unchanged for compatibility; rename the concept in the canonical model (`events_with_severity` vs `alerts`) and add separate alert-based KPIs later. | P2 | KPI semantics must not change silently |
| F-11 | `alert_config.LOCAL_UTC_OFFSET = 2`, `WORK_START=8`, `WORK_END=18`; `alert_engine.local_hour/is_occupied` | Fixed offset and hours for all buildings; `buildings` and `locations` carry no timezone or schedule. | Wrong for other sites; fixed offset ignores DST; “after hours” facts (`ACC_DOOR_FORCED`, `ACC_DENIAL_SPIKE`, `SEC_BLIND_SPOT_ACCESS`, anomaly regimes) depend on it. | `buildings.timezone` (IANA) + working hours (idea already in the repo’s draft schema); compute local hour per building. | P1 | Results change only if the chosen zone differs from fixed UTC+2 (e.g., DST periods) — **[Open]** |
| F-12 | `alert_engine.py:179,347`; `chat_lite.py:566` | `zone == "Server Room"` selects stricter thresholds and the motion rule; playbook action `HVAC_IT_NOTIFY` conditions on the same string. | Behaviour keyed on a display name. | `locations.zone_type` / `is_sensitive` columns. | P2 | Backfill `zone_type` for “Server Room” keeps results identical |
| F-13 | `system_types.code CHECK (...)`, `reference_data.SYSTEM_CODES`, `v_camera_status` (`asset_type='camera'`), `dashboard_core_pg` metric-name constants | Adding a building system requires a DDL change and code edits. | Violates “future building systems without code changes”. | Drop the CHECK in favour of catalogue rows; read system list from the DB. | P2 | None |
| F-14 | `app/adapters/database/db_adapter.py::store_alerts`; `db/migrations/005_alert_uniqueness.sql` | A database UNIQUE index now protects `(rule_id, asset_id, first_ts)` after duplicate pre-check. The background loop, `POST /alerts/run`, and `POST /alerts` can still execute concurrently; no advisory lock was verified. | The index prevents duplicate rows, but concurrent detection/incident assignment still needs a focused regression check. | Retain migration 005; test concurrent detection and consider an advisory lock only if that test demonstrates a remaining race. | P0 | No existing API contract change |
| F-15 | `event_records` uniqueness; migration 007 | Legacy rows keep `(asset_id, event_type_id, ts)` when `source_event_id IS NULL`; source events use `(source_id, source_event_id)`. | Avoid collisions between distinct connector events without changing legacy duplicate behavior. | Implemented; service returns DUPLICATE for exact retry and CONFLICT for changed content/time. | Complete | Existing rows unchanged |
| F-16 | `alert_engine.make_alert` (`detected_ts`) | For threshold rules `detected_ts` is the **first data timestamp of the run**, not processing time; `created_at` is processing time. | Misleading semantics for latency/SLAs. | Document; add `detected_at` (processing time) in canonical alert, keep `detected_ts` for compatibility. | P2 | None |
| F-17 | `app/domain/alerts/alert_engine.py` legacy functions | Some legacy functions refer to tables outside the runtime schema; active detection uses `app/adapters/database/db_adapter.py` and the current pipeline. | Dead/unused paths are easy to mistake for runtime behavior. | Keep the active pipeline path authoritative; remove dead functions only after import/call-site checks and tests. | P2 | None |
| F-18 | `legacy/dashboard_api.py`, `legacy/chat_engine.py`, `legacy/chat_api.py` | These modules query an older schema and are outside the active `app.main` router registration. | Confusing duplicates can be mistaken for supported routes. | Retain as legacy until call sites and migration history are reviewed; do not treat them as the operational API. | P2 | None |
| F-19 | `docs/data-model.sql`, `docs/DATA_MODEL.md`, ERD assets, `docs/ARCHITECTURE.md`, and API proposals | These describe earlier unified schemas and/or routes not mounted by the current application. `docs/DATA_MODEL.md` refers to a migration that is absent from this checkout. | Multiple drafts can be mistaken for the runtime model. | Treat the SQL bootstrap/migrations as runtime authority and this canonical model as the target proposal; keep older drafts clearly labeled and cross-linked. | P1 | None (docs) |
| F-20 | `deploy/compose/docker-compose.yml`; `db/migrations/006`; `scripts/apply_migrations.py` | Fresh volumes initialize 003–007; existing databases need the advisory-locked runner for 006+. | Init mounts alone do not upgrade existing volumes. | Implemented; apply only after backup/review using the procedure in `DATA_MIGRATION_PLAN.md`. | Complete | Deployment procedure |
| F-21 | `dashboard_core_pg.open_readonly`; `App.day2_connect`; `db_adapter.connect` | Three connection factories, two drivers (psycopg v3 and psycopg2). `open_readonly` is not actually read-only (comment only); default passwords `"change-me"` / `""`. No pooling. | Hygiene and security; per-request connections. | One connection module; genuine read-only role for read paths; env-required credentials. | P2 | None |
| F-22 | `chat_lite._safe_sql`, `_run_readonly_sql` | LLM tool runs model-written SQL on the app connection (`SET TRANSACTION READ ONLY`, 5 s timeout, string blocklist, `LIMIT 200`). Unscoped by building. | String blocklist is brittle (false positives/negatives); no tenant scoping; uses the application user. | Dedicated read-only DB role + building-scoped views before multi-customer use. | P1 | Chat must keep answering from `v_*` views |
| F-23 | `dashboard_core_pg` `heartbeat_compliance`; `alert_config.T['reading_gap_min']` | Hourly heartbeat and 15-minute cadence are global constants (`expected_slots = seconds // 3600 + 1`; `reading_gap_min=20`). | No per-asset expected interval; cannot represent assets that report every 5 s or every 6 h. | `reporting_expectations` per asset/metric. | P1 | KPI formula must still return the same value for current assets |
| F-24 | KPI `healthy_assets_pct` status enum (`critical`, `no_data`, `warning`, `unknown`, `normal`) | Silence (`no_data`) is a *health status*; `frontend_api` maps it to `WARNING`. | Equipment state and data freshness are mixed; a silent device is reported as unhealthy rather than “state unknown, data stale”. | Separate `operational_state` from `freshness` (`CANONICAL_DATA_MODEL.md` §8–9); keep the KPI’s existing output as a compatibility projection. | P1 | KPI output unchanged by design |
| F-25 | `telemetry_records`, `event_records` | Mutable (no append-only trigger); evidence references facts by id inside JSONB; FKs from alerts to facts do not exist. | Evidence integrity relies on re-verification at read time (which exists and is tested). | Normalised `alert_evidence` rows with FKs; keep JSON in `alerts.evidence` (dual-write). | P1 | None if dual-written |
| F-26 | `event_records` heartbeats (`event_types.name='heartbeat'`) | ≈ 950 heartbeat rows in the 78 h seed mixed with operational events; every consumer filters them out (`NOT_COUNTED_EVENTS`, KPI, chat). | Liveness mixed with operational history. | Keep as events for compatibility; add `last_seen_at` in `asset_state_current` so liveness does not require scanning events. | P2 | None |
| F-27 | Repository root | `.env` is present in the archive (git-ignored, not tracked; contents deliberately not read). Port 5432 published, no authentication, Socket.IO `cors_allowed_origins="*"` (`README.md` lists these). | Secret-handling and exposure risks if the archive is shared. | Exclude `.env` from hand-off archives; require auth before any non-demo deployment. | P2 | None |
| F-28 | `scenario_runs` (public schema) | Evaluation ground truth lives next to production tables; array columns have no FKs. | Mixed concerns. | Move to an `eval` schema (idea present in the repo’s draft). | P3 | None |

### 5.2 Coverage of the 12 requested finding categories

| Category | Findings |
|---|---|
| 1 Duplicate/overlapping structures | F-07, F-08, F-09, F-19, F-20 |
| 2 Naming inconsistencies | F-04, F-10 |
| 3 Duplicated business logic | F-07, F-08, F-09 |
| 4 Missing relationships/constraints | F-14, F-15, F-25, F-13 |
| 5 Timestamps/timezones | F-02, F-11, F-16 |
| 6 Missing building/tenant relationships | F-03 |
| 7 Hardcoded identifiers | F-11, F-12, F-13 |
| 8 Dependence on simulated data | F-05, F-06, §4.3 (anchor to `now()`), F-26 |
| 9 Vendor-specific assumptions | none inside the Core schema; `badge → card_id` rename in `db_adapter.DETAIL_RENAME` and port naming (`Gi1/0/24`) in seed `details` are the only traces. Gap is the *absence* of manufacturer/model/external-id fields (F-02, §7). |
| 10 Integrity/reliability risks | F-14, F-15, F-20, F-22, F-25 |
| 11 Components to reuse | §5.3 |
| 12 Components to consolidate | §5.4 |

### 5.3 Reuse (do not rewrite)

`alert_engine.py` rule functions and `alert_config.py` playbooks · `incident_engine.py` (grouping, lifecycle, ranking) · `evidence.py` (row re-verification) · `validation.py` (grounding contract) · `recommendations.py` · `ai_explainer.py` fallback logic · `db_adapter.guard/DatabaseUnavailable` · `dashboard_core_pg` KPI definitions and timestamp parser · the `004` views pattern · the existing test suites (§11).

### 5.4 Consolidate (later, behind parity tests)

Asset-health SQL (4 copies) · fault/recovery pairs (3 copies) · system-code translation (3 copies) · connection factories (3) · event/metric compatibility (Python + SQL) · legacy root modules · competing model documents.

---

## 6. Proposed target architecture [Proposed]

![Target architecture](./img/Proposed_target.png)

Principles applied: reuse existing tables and engines; add, don’t replace; a repository/view layer is the only thing engines read through; no new infrastructure.

## 7. Separation between Core, connectors, and plugins [Proposed]

| Concern | Core | Connector / plugin |
|---|---|---|
| Vendor protocol, SDK, auth to the vendor system | — | ✔ |
| Mapping vendor IDs → `external_id`; vendor payload → canonical fields | — | ✔ |
| **Unit conversion to the canonical unit** | validates (rejects unknown/mismatched unit) | ✔ converts |
| Timestamps converted to UTC, `observed_at` taken from the device/source | validates format/range, stamps `received_at` | ✔ |
| Store-and-forward buffering, retry with the same `message_id` | accepts retries idempotently | ✔ |
| Schema/contract validation, compatibility checks, dedupe, persistence | ✔ | — |
| State derivation, freshness, gaps, alerts, incidents, KPIs, AI | ✔ | — |
| Asset registry (internal ID, location) | ✔ owner | may *propose* assets via a provisioning call (later) |
| Vendor-specific fields | stored only in `source_metadata` / `asset.metadata` JSONB, never as columns | ✔ source |

The Core must not import any vendor SDK and must not branch on vendor name. The only vendor-shaped data it keeps is opaque metadata.

## 8. Multi-building design [Proposed]

- **Ownership:** `organizations (1) → buildings (n) → locations (tree) → assets`. Isolation key for every operational record is **`building_id`**; `organization_id` lives only on `buildings`. Single-customer installs run with one default organization — no multi-tenancy machinery beyond the foreign key.
- **Every operational row carries `building_id`** (telemetry, events, state, alerts, incidents, evidence), kept consistent with the asset’s building by a composite FK `(asset_id, building_id) → assets`.
- **All repository functions take a building scope** (`NULL` = all buildings the caller may see). Default behaviour equals today’s.
- **Per-building settings:** IANA timezone, working hours (replacing the global offset).
- **Data clock per building** (`as_of` computed per building) so one silent building cannot mask or distort another.
- Row-level security or schema-per-customer are **not** proposed now; revisit when a second customer shares a database (**[Open]** Q3).

## 9. Canonical data ownership

| Data | Owner (writes) | Readers |
|---|---|---|
| Organizations, buildings, locations, systems, assets, catalogues | Core admin/provisioning | everyone |
| `telemetry_records`, `event_records` | New batch service plus current seed/legacy `POST /events` during transition | engines, KPI, chat, historian queries |
| `data_sources`, `ingestion_batches`, `ingestion_rejections` | Ingestion service | operations, data-quality views |
| `equipment_state_records` | Ingestion service, append-only reported states | state consumers; freshness/job remains future |
| `alerts`, `alert_evidence` | Alert engine (`pipeline.detect`) | everyone; status changes via incident/alert lifecycle API |
| `incidents` | Incident engine | everyone |
| `alerts.explanation` | AI explain pipeline | UI |

## 10. Authoritative runtime data model

| Layer | Definition |
|---|---|
| **Pre-006 deployed baseline** | Base schema plus migrations 003–005: 13 tables and 5 views, verified read-only in the shared database on 18 November 2026. |
| **Repository schema target** | The pre-006 schema plus migrations 006–007: 20 tables and 7 views, verified in an isolated test database. |
| **Migration state** | Migrations 006–007 are included on fresh init and have an explicit runner for existing databases; the shared database has not been migrated. |
| **Compatibility mechanisms** | Nullable additive columns; `v_telemetry_valid`; existing `db_adapter` translation; legacy APIs remain supported; isolated migration and route regressions. |
| **Not authoritative** | `data-model.sql`, `DATA_MODEL.md`, `ERD-new.png`, `NEW-ERD.png`, `docs/ARCHITECTURE.md`, `docs/API_CONTRACT.md`, `dashboard_api.py`, `chat_engine.py`, `chat_api.py`, legacy functions in `alert_engine.py`. |

## 11. Existing intelligence compatibility

Summary (full mapping in `DATA_MIGRATION_PLAN.md` §1 and §5):

| Component | Reads today | Impact of the proposal | Adaptation |
|---|---|---|---|
| KPI engine (`dashboard_core_pg`) | `assets`, `system_types`, `locations`, `event_records`, `event_types`, `telemetry_records`, `metric_types` | None for M1–M3 (tables and columns remain). Healthy-assets output is preserved as a projection of state + freshness. | None initially; later swap SQL for repository calls with parity tests |
| Alert engine | via `db_adapter.load_context` → engine-shaped event dicts (`EVENT_MAP`, `METRIC_MAP`) | None. Quality filter (exclude `BAD`) is a no-op on existing rows (all `GOOD`). | Provisional bandwidth percentages: F-05; timezone/zone_type: F-11/F-12 |
| Incident engine | `alerts`, `incidents`, `assets`, `locations` | Gains `building_id`; grouping rule unchanged | None |
| Evidence validation | `alerts.evidence` JSON → re-read `event_records`/`telemetry_records` by id | Unchanged; `alert_evidence` table added in parallel | Dual-write |
| AI / recommendations | `alerts`, evidence, `alert_config.PLAYBOOKS` | Unchanged | None |
| Chat assistant | `v_*` views + KPI functions + read-only SQL tool | Views kept (names/columns stable); building scope added later | Views gain `building_id` columns (additive) |
| Dashboards | `frontend_api` endpoints | Unchanged response shapes | None |

Changes that would **alter results**: F-05 bandwidth thresholds (implemented and regression verified; calibration remains); F-11 timezone if the zone ≠ fixed UTC+2 in DST periods; F-07 reconciliation of `v_asset_overview` with the KPI definition.

## 12. Architecture decisions and rationale

| ID | Decision | Rationale |
|---|---|---|
| AD-01 | Keep PostgreSQL as the Historian store; no TimescaleDB/Kafka/Redis | Seed scale is ~5 k telemetry rows; design goals are correctness and traceability, not throughput. Re-evaluate on measured need (`HISTORIAN_DESIGN.md` §14). |
| AD-02 | Evolve existing tables additively; keep names | Zero breaking change for ~15 modules that hard-code the names. |
| AD-03 | `building_id` on every operational row; `organization_id` only on `buildings` | Simple, explicit isolation without RLS. |
| AD-04 | Canonical system codes = current DB codes (`NETWORKING`, `ACCESS_CONTROL`); translation to UI names stays at the API edge | They are already persisted, FK targets, and accepted by the KPI API. |
| AD-05 | Connectors convert units and timestamps; Core validates | Keeps the Core vendor-free; the rule is testable at the boundary. |
| AD-06 | State (`operational_state`, `availability`, `communication_status`) is separate from `freshness`; missing telemetry never implies offline | Required by the product brief. |
| AD-07 | “Now” = wall clock for live evaluation, explicit `as_of` for replay/demo | Allows stale-feed detection without breaking simulated-data demos. |
| AD-08 | Idempotency by natural key (existing UNIQUE) + `source_id/source_event_id` for events | Reuses what exists; fixes F-15. |
| AD-09 | Evidence: keep JSON, add normalised FK-backed `alert_evidence` (dual-write) | Preserves `evidence.py` behaviour and adds referential integrity. |
| AD-10 | No dead-letter queue service; a PostgreSQL `ingestion_rejections` table is enough initially | “Dead-letter if justified”: audit need is real, a broker is not. |
| AD-11 | No rewrite of KPI/alert/incident/AI engines | No evidence it is necessary; tests exist. |

## 13. Risks and open technical questions

**Risks**
1. F-05 is addressed and regression verified. Its provisional percent thresholds may create future network alerts and change packet-loss explanations; seed data currently remains below the warning threshold.
2. Backfilling `building_id`/`received_at` on large tables must be batched; `received_at` for legacy rows is **unknown** and must stay `NULL` (do not invent).
3. Partitioning later constrains FKs to `telemetry_records` (see `HISTORIAN_DESIGN.md` §14).
4. Detection loop inside the API process will duplicate under multiple workers until F-14 is fixed.
5. Local changes (`chat_facts.py`, `chat_lite.py`, compose, `004`) are uncommitted: any migration work must be coordinated with their owner so they are not lost.

**Open questions (need eng.Mohsen / Amr)**
- **Q1** Canonical unit for `bandwidth_util`: resolved to percent for the current runtime. Provisional thresholds remain subject to calibration when link capacity is recorded (F-05).
- **Q2** Which IANA timezone and working hours apply to Main Tower and Data Center Annex? (F-11)
- **Q3** Is a second customer expected to share one database? (decides RLS vs schema/DB per customer)
- **Q4** Batch semantics: atomic (as in `docs/API_CONTRACT.md`, 409 on any duplicate) or per-record results? This document proposes per-record results; confirm.
- **Q5** Are “campus/site” levels needed, or is `locations.parent_location_id` sufficient?
- **Q6** Should KPI “Alerts raised” keep counting severity events, or move to `alerts`? (F-10)
- **Q7** Does `app/chat_lite.py`’s SQL tool remain, or be restricted to the views only? (F-22)
- **Q8** Ownership of playbook approvals (`DEFAULT_APPROVER` is a placeholder).
