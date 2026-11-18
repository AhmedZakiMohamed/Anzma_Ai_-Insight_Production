# CONTRACT_TO_DB_MAPPING.md — Anzma AI Insight Core

**Status:** Proposal. **No migration is written and no database object is changed by this document.**
**Purpose:** show how every field of [`INGESTION_CONTRACT.md`](INGESTION_CONTRACT.md) lands in the **existing** PostgreSQL schema, and list the minimum that must be added. Rule followed: **no parallel data model, no duplicated data** without a stated reason.
**Basis [Verified]:** `db/init/anzma_schema_and_seed.sql`, `db/migrations/003`–`005`, `app/main.py`, `app/api/*`, `app/repositories/dashboard_core_pg.py` in `Anzma_Ai_Insight_Production`.

Tags: **[Verified]** read in the repository, **[Proposed]**, **[Open]**.

---

## 1. Summary

| Item | Decision |
|---|---|
| **Existing tables reused** | `buildings`, `locations`, `system_types`, `assets`, `metric_types`, `event_types`, `metric_type_system_compat`, `event_type_system_compat`, `telemetry_records`, `event_records`. `alerts`, `incidents`, `scenario_runs` are **untouched**. |
| **Existing columns that already cover the contract** | `telemetry_records`: `asset_id`, `metric_id`, `ts` (= `observed_at`), `value`, unique key `(asset_id, metric_id, ts)`, finite-value CHECK. `event_records`: `asset_id`, `event_type_id`, `ts` (= `observed_at`), `severity`, `details`. `assets.asset_code` (contract `asset_code`). `metric_types.name`/`unit` (`metric_code`/`unit`). `event_types.name`. Both compatibility tables (the "no unrelated event on incompatible equipment" rule). |
| **New columns** | `telemetry_records`: `source_id`, `ingestion_batch_id`, `received_at`, `quality`, `contract_version`, `source_metadata`. `event_records`: `source_id`, `ingestion_batch_id`, `source_event_id`, `received_at`, `contract_version`, `source_metadata`. `buildings`: `building_code`. Optional: `metric_types.min_plausible`, `max_plausible`. |
| **New tables** | **Required for ingestion:** `data_sources`, `source_credentials`, `asset_external_ids`, `ingestion_batches`, `ingestion_rejections` (+ one view for unmapped IDs). **Deferred to the freshness/state phase:** `reporting_expectations`, `data_gaps`, `asset_state_current`, `asset_state_history`. |
| **Existing APIs reused** | All dashboard, alert, incident and chat endpoints keep their shape. Their **queries** must exclude `quality = 'BAD'` (internal change, no API change). `POST /events` stays as a legacy path with one code change (§6). |
| **New APIs** | `POST /api/v1/ingest/batches`, `GET /api/v1/ingest/batches/{batch_id}` (required). Source/credential/mapping administration and a source-health read (later, §7). |

What is **not** added on purpose: no new telemetry or event table, no second asset registry, no separate "connector" copy of buildings or metrics, no unit-conversion table, and no changes to `alerts`/`incidents`/evidence.

---

## 2. What already exists [Verified]

| Table | Relevant columns / constraints today |
|---|---|
| `buildings` | `building_id`, `name UNIQUE`. **No code, no time zone.** |
| `locations` | `location_id`, `building_id`, `floor`, `name`, `UNIQUE (building_id, name)` |
| `system_types` | `code` CHECK in `HVAC, NETWORKING, ACCESS_CONTROL, CCTV, FIRE_ALARM` |
| `assets` | `asset_id`, `asset_code UNIQUE`, `name`, `asset_type`, `system_type_id`, `location_id` (building reached through the location) |
| `metric_types` | `metric_id`, `name UNIQUE`, `unit` (8 metrics: temperature `C`, humidity `%`, power_kw `kW`, cpu_load `%`, packet_loss `%`, bandwidth_util `%`, smoke_level `%/m`, camera_fps `fps`) |
| `event_types` | `event_type_id`, `name UNIQUE` (12 types, including `heartbeat`, valid for every system) |
| `*_system_compat` | PK `(metric_id/event_type_id, system_type_id)` |
| `telemetry_records` | `telemetry_id`, `asset_id`, `metric_id`, `ts`, `value`, `UNIQUE (asset_id, metric_id, ts)`, finite CHECK |
| `event_records` | `event_id`, `asset_id`, `event_type_id`, `ts`, `severity` (lowercase `info/warning/critical`), `details JSONB`, `UNIQUE (asset_id, event_type_id, ts)` |
| `alerts`, `incidents` | Operational layer, unrelated to ingestion |

Also verified: no table or column named `source_id`, `external_asset`, `received_at`, `batch_id` or `quality` exists in `db/`, and none of those names is used in `app/` for stored data (the word `quality` appears once in `alert_config.py`, unrelated); `POST /events` is the only write endpoint for facts; the schema comment states the UTC-`Z`, no-future-timestamp and batch rules are enforced "in ingestion code, not here" — and that code does not exist yet.

---

## 3. Field-by-field mapping

### 3.1 Envelope

| Contract field | Lands in | Status |
|---|---|---|
| `contract_version` | `ingestion_batches.contract_version`; copied to fact rows | New |
| `batch_id` (UUID) | `ingestion_batches.client_batch_id`, `UNIQUE (source_id, client_batch_id)` | New table |
| `sent_at` | `ingestion_batches.sent_at` | New |
| `delivery_mode` | `ingestion_batches.delivery_mode` | New |
| `source.source_code` | `data_sources.source_code` → every fact's `source_id` | New table |
| `source.connector.name/version` | `data_sources.connector_name/version`, `ingestion_batches.connector_name/version` | New |
| `source.connector_status` | `ingestion_batches.connector_status` (JSONB) and `data_sources.last_*` | New |
| `building_code` | `buildings.building_code` (new column) → checked against `data_sources.building_id` | New column |
| `records` count and outcome | `ingestion_batches.received/accepted/duplicate/rejected_count`, `status`, stored `result` for replay | New |
| Batch body hash | `ingestion_batches.payload_sha256` (detects `BATCH_ID_REUSED`) | New |
| Receive time | `ingestion_batches.received_at`, copied to fact rows | New |

### 3.2 Telemetry record

| Contract field | Lands in | Status |
|---|---|---|
| `asset.asset_code` | `assets.asset_code` → `telemetry_records.asset_id` | Existing |
| `asset.external_id` | `asset_external_ids (source_id, external_id)` → `asset_id` | New table |
| `metric_code` | `metric_types.name` → `telemetry_records.metric_id` | Existing |
| `value` | `telemetry_records.value` | Existing |
| `unit` | **Validated** against `metric_types.unit`; not stored (it always equals the canonical unit) | Existing |
| `observed_at` | `telemetry_records.ts` | Existing |
| `quality` | `telemetry_records.quality` | **New column** |
| `source_metadata` | `telemetry_records.source_metadata` | **New column** |
| *(Core)* `received_at` | `telemetry_records.received_at` | **New column** |
| *(Core)* provenance | `telemetry_records.source_id`, `ingestion_batch_id`, `contract_version` | **New columns** |
| System compatibility | `metric_type_system_compat` | Existing |
| Duplicate / conflict | existing `UNIQUE (asset_id, metric_id, ts)` | Existing |

### 3.3 Event record

| Contract field | Lands in | Status |
|---|---|---|
| `asset.*` | as above | Existing / New table |
| `event_type` | `event_types.name` → `event_records.event_type_id` | Existing |
| `observed_at` | `event_records.ts` (= event time) | Existing |
| `severity` | `event_records.severity` (uppercase on the wire → lowercase stored) | Existing |
| `details` | `event_records.details` | Existing |
| `source_event_id` | `event_records.source_event_id` | **New column** |
| `source_metadata` | `event_records.source_metadata` | **New column** |
| *(Core)* provenance and `received_at` | `source_id`, `ingestion_batch_id`, `received_at`, `contract_version` | **New columns** |
| System compatibility | `event_type_system_compat` | Existing |
| Duplicate / conflict | **new** `UNIQUE (source_id, source_event_id)`; see §5 | **Constraint change** |

### 3.4 State record **[Proposed]**

A state report is stored as an ordinary row in `event_records`, not in a new table.

| Contract field | Lands in |
|---|---|
| (type) | `event_type = 'equipment_state_report'` — one new `event_types` row, made compatible with all five systems exactly as `heartbeat` is |
| `observed_at` | `ts` |
| `reported_state`, `availability`, `reason_code`, `message` | `details` JSONB |
| (severity) | `info` |
| (idempotency key) | `source_event_id` generated by the Core as `state\|<asset_code>\|<observed_at>` so state reports share the events' unique key |
| `source_metadata` | `source_metadata` |

**Why not a new table:** it reuses the validation, compatibility, idempotency, provenance and retention already built for events, and matches `CANONICAL_DATA_MODEL.md` §10 (`event_class = STATE_REPORT`). Typed state tables (`asset_state_current`, `asset_state_history`) remain the destination of the **Core-derived** state in the later phase; they will read these rows as evidence.
**Cost:** state fields are not typed columns (queried through `details ->>`). Acceptable until the state evaluator exists. **Check before building:** event readers that count "all events in a window" must treat `equipment_state_report` like they already treat `heartbeat`.

---

## 4. New columns

All are added nullable (or with a default) so that existing rows and code keep working.

| Table | Column | Type | Default / rule |
|---|---|---|---|
| `buildings` | `building_code` | `TEXT` | `UNIQUE NOT NULL` after backfill; admin supplies codes (for example `MAIN-TOWER`, `DATA-CENTER-ANNEX`) |
| `telemetry_records` | `source_id` | `BIGINT` FK → `data_sources` | backfill `legacy-seed`, then `NOT NULL` |
| | `ingestion_batch_id` | `BIGINT` FK → `ingestion_batches` | `NULL` for legacy rows |
| | `received_at` | `TIMESTAMPTZ` | `NULL` = unknown (legacy). **Never back-filled with a guess.** |
| | `quality` | `TEXT NOT NULL` | default `'GOOD'`, CHECK `IN ('GOOD','UNCERTAIN','BAD')` |
| | `contract_version` | `TEXT` | |
| | `source_metadata` | `JSONB` | |
| `event_records` | `source_id`, `ingestion_batch_id`, `received_at`, `contract_version`, `source_metadata` | as above | |
| | `source_event_id` | `TEXT` | `NULL` for legacy rows |
| `metric_types` | `min_plausible`, `max_plausible` | `DOUBLE PRECISION` | optional; enables Core-assigned `BAD` |

Indexes: `telemetry_records (source_id, received_at)`; `event_records (source_id, received_at)`.

Deliberately **not added now** (in `CANONICAL_DATA_MODEL.md` but not needed by the ingestion path): `organizations`, `building_id` denormalised onto facts and assets, `buildings.timezone` and work hours (ISS-008), asset parent/manufacturer/lifecycle columns, `event_types.event_class` / `resolves_event_type_id`, `unit_conversions`, `value_kind`. Each remains valid for its own sprint item; none blocks ingestion.

---

## 5. Constraint change that needs a decision

Today `event_records` has `UNIQUE (asset_id, event_type_id, ts)`. If it stays, two **distinct** connector events on the same asset, of the same type, at the same instant, collapse into one (ISS-007), and a no-target `ON CONFLICT DO NOTHING` would misreport the second as a duplicate.

**[Proposed]**

1. Add `UNIQUE (source_id, source_event_id) WHERE source_event_id IS NOT NULL`.
2. Replace the legacy constraint with a **partial** unique index `(asset_id, event_type_id, ts) WHERE source_event_id IS NULL`, so legacy rows keep today's protection.

**Consequence [Verified]:** `POST /events` in `app/main.py` uses `ON CONFLICT (asset_id, event_type_id, ts) DO NOTHING`. With a partial index, PostgreSQL requires the index predicate in that clause (`… WHERE source_event_id IS NULL`), so the statement must change. Tests that insert events (`tests/…`, `e2e_s1.py`) must be checked for the same pattern.

`telemetry_records` keeps `UNIQUE (asset_id, metric_id, ts)` unchanged.

---

## 6. New tables

| Table | Purpose | Key columns |
|---|---|---|
| `data_sources` | Registry of sources | `source_id`, `source_code UNIQUE`, `building_id` FK (NULL allowed only for types `CORE`, `SIMULATOR`), `source_type` (`CONNECTOR/SIMULATOR/MANUAL/API/CORE`), `connector_name`, `connector_version`, `status` (`ACTIVE/DEGRADED/ERROR/DISABLED`), `keepalive_interval_s` (300), `delay_threshold_s` (900), `max_backdate_days` (90), `last_received_at`, `last_success_at`, `last_error_at`, `last_error`, `source_metadata` |
| `source_credentials` | API keys (D5) | `credential_id`, `source_id` FK, `key_prefix UNIQUE`, `key_hash`, `scopes TEXT[]`, `status` (`ACTIVE/REVOKED`), `expires_at`, `created_at`, `last_used_at` |
| `asset_external_ids` | Vendor ID → asset | PK `(source_id, external_id)`, `asset_id` FK, index `(asset_id)` |
| `ingestion_batches` | Batch audit and replay | `ingestion_batch_id`, `source_id`, `client_batch_id UUID`, `UNIQUE (source_id, client_batch_id)`, `payload_sha256`, `contract_version`, `delivery_mode`, `sent_at`, `received_at`, `status` (`ACCEPTED/PARTIAL/REJECTED`), `received/accepted/duplicate/rejected_count`, `connector_status JSONB`, `result JSONB` |
| `ingestion_rejections` | Minimal dead-letter, audit | `rejection_id`, `ingestion_batch_id` FK, `source_id`, `record_index`, `record_type`, `external_id`, `reason_code`, `message`, `payload JSONB` (truncated to 8 KiB), `created_at`; index `(source_id, reason_code, created_at)` |
| view `v_unmapped_external_assets` | What to map next | from `ingestion_rejections` where `reason_code = 'ASSET_UNRESOLVED'`: `source_id`, `external_id`, `count`, `first_seen`, `last_seen` |

Seed rows: `legacy-seed` (type `SIMULATOR`, all existing facts), `legacy-api` (type `API`, rows written by `POST /events`), `core-engine` (type `CORE`).

**Why each is not a duplicate:** nothing in the current schema records provenance, identity mapping, batch results or rejected input. `scenario_runs` is a simulation harness and is unrelated to batches.

**Retention [Open]:** `ingestion_rejections` can grow quickly if a connector misbehaves; propose 90 days and a per-source cap. Also **[Open]** whether `ingestion_batches.result` is kept past a shorter window.

### Deferred tables (freshness/state phase)

| Table | Why deferred |
|---|---|
| `reporting_expectations` | Needed to **compute** freshness, not to **accept** data. Defaults (900 s telemetry, 3600 s heartbeat) are enough to begin. |
| `data_gaps` | Written by the freshness job. `SOURCE_DECLARED_MISSING` is removed (D1). |
| `asset_state_current`, `asset_state_history` | Core-derived (D2). |

---

## 7. APIs

### 7.1 Existing

| Endpoint | Decision |
|---|---|
| `GET /health`, `GET /ready` | Reuse. Optionally report ingestion/database readiness. |
| `GET /dashboard/overview`, `/trends`, `/equipment-health` | **Reuse; no response change.** Underlying queries exclude `quality = 'BAD'`. |
| `GET /alerts`, `GET /alerts/{id}`, `POST /alerts`, `POST /alerts/run`, `POST /alerts/{id}/explain` | Reuse unchanged. Alert detection reads telemetry through the same exclusion. |
| `GET /incidents`, `/incidents/{id}`, `/incidents/{id}/evidence`, `POST /incidents/{id}/status` | Reuse unchanged. |
| `POST /chat`, `GET /chat/questions`, `POST /ask` | Reuse unchanged. Chat views (`004_chat_views.sql`) must apply the same exclusion. |
| `POST /events` | **Keep as legacy.** One change: the `ON CONFLICT` target (§5). Stamp rows with source `legacy-api`. Not for connectors (internal `asset_id`, no provenance). |
| Socket.IO `new_alert` | Unchanged. |

**Quality exclusion — scope.** [Verified] about 30 lines reference `telemetry_records` across 9 application files, plus `db/migrations/004_chat_views.sql`. **[Proposed]** introduce one view (for example `v_telemetry_valid`: `quality <> 'BAD'`) and point readers at it, rather than repeating the predicate 30 times. `UNCERTAIN` stays included; alert evidence marks it.

### 7.2 New

| Priority | Endpoint | Purpose |
|---|---|---|
| **P0** | `POST /api/v1/ingest/batches` | Ingestion (contract §2–§12) |
| **P0** | `GET /api/v1/ingest/batches/{batch_id}` | Stored result and full rejection list |
| P1 | Source registration, credential create/revoke, asset-mapping create/list, `GET …/unmapped` | Administration. **[Open C5]** The Core has no admin authentication today, so the first version may be a CLI or SQL script rather than an HTTP API. |
| P2 | Source health and freshness read (or extend `/dashboard/equipment-health`) | Needs the freshness job. |

Path prefix `/api/v1` is **[Open]** (platform owner).

---

## 8. Proposed migration `006_ingestion_foundation.sql` (outline only)

Numbering follows the existing sequence (`003`, `004`, `005`). Each step idempotent, like `005`.

1. Create `data_sources`; seed `legacy-seed`, `legacy-api`, `core-engine`.
2. Add `buildings.building_code`; backfill from administrator-supplied codes; then `UNIQUE NOT NULL`.
3. Add nullable columns to `telemetry_records` and `event_records` (`quality` with default).
4. Backfill `source_id` (`legacy-seed`); set `NOT NULL`; leave `received_at` NULL.
5. Create `ingestion_batches`, `ingestion_rejections`, `asset_external_ids`, `source_credentials`; add FKs from the fact tables.
6. Event uniqueness change (§5): create the new partial unique index, then swap the legacy constraint.
7. Insert `equipment_state_report` into `event_types` and its compatibility rows.
8. Create `v_telemetry_valid` and `v_unmapped_external_assets`.
9. Extend `scripts/verify_schema.sql` with checks for each new object and for orphan/compatibility rules.

**Regression protection:** existing KPI, alert, incident, evidence and chat tests (`test_data_integrity.py`, `test_frontend_api.py`, `test_day2_integration.py`, `test_chat_consistency.py`, `e2e_s1.py`) must pass unchanged on a migrated database. Legacy rows keep `quality = 'GOOD'`, so current results must not move.

---

## 9. Risks and open points

| # | Item | Impact |
|---|---|---|
| M1 | Event unique-index swap breaks `POST /events` unless its `ON CONFLICT` is updated | High, easy to miss |
| M2 | Readers not excluding `BAD` would let bad readings into KPIs and alerts | High; hence the single view |
| M3 | Event counters must ignore or classify `equipment_state_report` | Medium |
| M4 | Telemetry key lacks `source_id`: two sources writing the same asset/metric/time conflict (first wins) | Medium; avoid by one authoritative source per asset series |
| M5 | `ingestion_rejections` growth | Medium; cap and retention |
| M6 | No admin authentication for source/credential/mapping management | Medium |
| M7 | `bandwidth_util` unit (`%` stored, rules use Mbps — ISS-003) will surface as `UNIT_MISMATCH` the first time a real connector reports bandwidth | Medium; resolve before connecting a network system |
| M8 | Day-of-migration building codes need an administrator decision | Low |

Open decisions carried from the contract: P8 (path prefix), P9 (value corrections), P10 (version deprecation window), C5 (admin interface).
