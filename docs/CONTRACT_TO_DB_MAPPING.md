# CONTRACT_TO_DB_MAPPING.md — Anzma AI Insight Core

**Status:** Reconciled with implemented additive migrations `006_ingestion_foundation.sql` and `007_event_source_identity.sql`; the shared existing database has not been migrated. This document itself makes no database changes.
**Purpose:** show how every field of [`INGESTION_CONTRACT.md`](INGESTION_CONTRACT.md) lands in the **existing** PostgreSQL schema, and list the minimum that must be added. Rule followed: **no parallel data model, no duplicated data** without a stated reason.
**Basis [Verified]:** `db/init/anzma_schema_and_seed.sql`, `db/migrations/003`–`005`, `app/main.py`, `app/api/*`, `app/repositories/dashboard_core_pg.py` in `Anzma_Ai_Insight_Production`.

Tags: **[Verified]** read in the repository, **[Implemented]** present in migration/code, **[Proposed]** future only.

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
| `event_records` | Pre-007: full UNIQUE `(asset_id, event_type_id, ts)`. Migration 007 replaces it with a partial unique index on that key WHERE `source_event_id IS NULL`; source rows use UNIQUE `(source_id, source_event_id) WHERE source_event_id IS NOT NULL`. |
| `alerts`, `incidents` | Operational layer, unrelated to ingestion |

Historical pre-006 baseline note: before migrations 006–007, no table or column named `source_id`, `external_asset`, `received_at`, `batch_id` or stored `quality` existed, and `POST /events` was the only write endpoint for facts. The UTC-`Z`, no-future-timestamp and batch rules were not enforced in that baseline. This note describes the prior state; the implemented v1 path is mapped below.

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

## 5. Implemented identity and uniqueness constraints

Migration 006 adds a partial unique index on `(source_id, source_event_id)` for non-NULL source IDs. Migration 007 replaces the legacy full unique constraint with a partial unique index on `(asset_id, event_type_id, ts) WHERE source_event_id IS NULL`; the existing rows remain unchanged. This lets distinct source event IDs coexist at the same asset/type/time while retaining the legacy key. `POST /events` uses the partial key after 007 and falls back to the prior full key when the partial index is absent. Telemetry keeps `UNIQUE (asset_id, metric_id, ts)`. Batch identity is `UNIQUE (source_id, batch_id)`; the payload hash detects reuse of a batch ID with changed content. The service compares conflicting natural-key records and never updates an accepted fact.

`asset_external_ids` has primary key `(source_id, external_id)`. A database trigger verifies each mapped asset belongs to the source's building. A source cannot remap an existing external ID to another asset; provisioning reports a conflict and makes no update.

## 6. Implemented ingestion and security tables

| Table | Purpose | Key columns / invariant |
|---|---|---|
| `data_sources` | Registered source/building/connector | `source_code UNIQUE`, required `building_id`, connector identity and status |
| `source_credentials` | Per-source bearer credentials | key prefix lookup; SHA-256 hash only; scopes, expiry, revocation and last-use timestamps |
| `asset_external_ids` | Source-local external asset identity | PK `(source_id, external_id)`; trigger enforces source/building match |
| `ingestion_batches` | Batch result and exact retry record | unique source + UUID; canonical JSON SHA-256; per-record counts and response |
| `ingestion_rejections` | Durable rejected-record audit | batch/index unique; reason and payload; excluded from operational facts |
| `equipment_state_records` | Append-only reported equipment state | unique source + asset + observed time; accepted state only |

Provisioning uses `scripts/ingestion_admin.py`; it prints a newly issued key once. Rotation issues an additional active key and revocation is explicit. No API key is stored in batch payloads or rejection details.

## 7. Implemented API mapping

The v1 contract is implemented at `POST /api/v1/ingest/batches` and `GET /api/v1/ingest/batches/{batch_id}`. Existing `POST /events` and `POST /alerts` are unchanged. The full envelope, record schemas, HTTP statuses, response meanings, and retry rules are in `INGESTION_CONTRACT.md`.

Accepted measurements/events/state rows, the batch result, and rejection audit commit in one transaction. `200 PARTIAL` means those outcomes were committed. `422 REJECTED` means the rejection result was committed and is auditable. Database failure rolls the transaction back and returns retryable `503`. Exact same-body retry with the same `(source_id, batch_id)` replays the stored response; changed body with that key returns `409 BATCH_ID_REUSED`.

## 8. Implemented additive migration and rollout

`db/migrations/006_ingestion_foundation.sql` and `007_event_source_identity.sql` add/adjust constraints without backfilling or updating/deleting historical fact rows. New provenance/quality columns remain NULL on old facts. `v_telemetry_valid` excludes `quality='BAD'` and keeps legacy NULL quality. Existing operational readers were switched to this view; evidence still reads the original telemetry table and record IDs.

`deploy/compose/docker-compose.yml` applies 006 and 007 only when initializing an empty volume. Existing databases use `scripts/apply_migrations.py`, which applies unapplied 006+ migrations under a PostgreSQL advisory lock and records versions in `schema_migrations`. The exact operator procedure and compatible rollback are documented in `DATA_MIGRATION_PLAN.md`. Migrations 006/007 have not been run against the shared existing database.

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
