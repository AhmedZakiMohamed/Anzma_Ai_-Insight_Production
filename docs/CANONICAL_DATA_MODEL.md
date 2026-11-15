# CANONICAL_DATA_MODEL.md — Anzma AI Insight Core

| | |
|---|---|
| **Status** | **Proposed specification.** Nothing here is implemented unless marked **[Existing]**. |
| **Baseline** | The 13-table runtime schema described in `CORE_DATA_ARCHITECTURE.md` §4 (`db/init/anzma_schema_and_seed.sql` + `003_incidents.sql`, views in `004_chat_views.sql`). |
| **Related** | `CORE_DATA_ARCHITECTURE.md`, `HISTORIAN_DESIGN.md`, `DATA_MIGRATION_PLAN.md`, `SPRINT_1_TECHNICAL_ISSUES.md` |

Notation: **[Existing]** table/column exists today · **[NEW column]** added to an existing table · **[NEW entity]** new table · **[View]** proposed view · **[Open]** needs a decision.
No DDL here is a migration; the column lists are specifications for review.

---

## 1. Design principles

1. **Evolve, don’t replace.** Existing table and column names stay. Additions are nullable-first and backward compatible.
2. **Vendor independence.** The Core stores *what happened* in standard terms. Vendor identity appears only as `data_sources`, `asset_external_ids`, manufacturer/model attributes and opaque `*_metadata` JSONB — never as Core columns that drive logic.
3. **Observed time ≠ received time.** Every fact has `ts` (observed) and `received_at` (platform clock).
4. **State ≠ freshness.** A silent device is “state last known, data stale”, never automatically “offline”.
5. **Building identity is explicit** on every operational row (`building_id`).
6. **Traceability.** Every alert/evidence row resolves to stored fact rows, which resolve to a source and batch.
7. **Explicit handling of bad data.** Invalid → rejected and logged; implausible → stored with quality `BAD`; missing → a recorded gap; duplicate → idempotent no-op; late → flagged.
8. **Configuration is data.** Units, plausible ranges, expected intervals, zone types, working hours and retention live in tables, not code constants.
9. **No cascades.** All FKs are `ON DELETE RESTRICT`.
10. **Simplicity.** Eleven new tables at most; each justified in §2.

---

## 2. Entity definitions

### 2.1 Summary

| Entity | Status | Purpose |
|---|---|---|
| `organizations` | **[NEW entity]** | Customer/owner of buildings. One default row for single-customer installs. |
| `buildings` | [Existing] + new columns | Isolation unit. |
| `locations` | [Existing] + new columns | Hierarchical physical locations (floor/zone/room). |
| `system_types` | [Existing] (CHECK relaxed) | Building-system catalogue (HVAC, CCTV, …). |
| `assets` | [Existing] + new columns | Equipment, manufacturer-independent. |
| `asset_external_ids` | **[NEW entity]** | Vendor asset IDs per source → internal asset. |
| `data_sources` | **[NEW entity]** | Connectors/simulators/manual feeds. |
| `metric_types` | [Existing] + new columns | Measurement catalogue with canonical unit and plausible range. |
| `unit_conversions` | **[NEW entity]** (optional) | Registered conversions the Core may apply (initially empty). |
| `event_types` | [Existing] + new columns | Event catalogue with class and recovery link. |
| `metric_type_system_compat`, `event_type_system_compat` | [Existing] | Which metric/event is valid on which system. |
| `telemetry_records` | [Existing] + new columns | Time-series measurements (the Historian fact table). |
| `event_records` | [Existing] + new columns | Operational events. |
| `reporting_expectations` | **[NEW entity]** | Expected reporting interval per asset/metric. |
| `asset_state_current` | **[NEW entity]** | One row per asset: current state + freshness. |
| `asset_state_history` | **[NEW entity]** | Append-only state transitions. |
| `data_gaps` | **[NEW entity]** | Detected missing-data intervals. |
| `alerts` | [Existing] + new columns | Detected conditions. |
| `incidents` | [Existing] + new columns | Managed cases grouping alerts. |
| `alert_evidence` | **[NEW entity]** | FK-backed evidence rows per alert. |
| `ingestion_batches` | **[NEW entity]** | One row per received batch (idempotency, reconciliation). |
| `ingestion_rejections` | **[NEW entity]** | Rejected records (audit; minimal dead-letter). |
| `retention_policies` | **[NEW entity]** (config) | Configurable retention per data class (§ in `HISTORIAN_DESIGN.md`). |
| `scenario_runs` | [Existing] | Test harness; to move to an `eval` schema later. |

### 2.2 Event vs Alert vs Incident

| | Event | Alert | Incident |
|---|---|---|---|
| Meaning | **Something happened** (fact) | **A detected condition needs attention** (judgement by a rule) | **A related operational problem/case** a person works on |
| Table | `event_records` | `alerts` | `incidents` |
| Mutable? | No (append-only) | Status/ack/resolution fields only | Status fields, title, severity, time range |
| Created by | Device/connector, or Core engine (source `core-engine`) | Alert engine (`pipeline.detect`) or manual `POST /alerts` | Incident engine (`assign_incidents`) |
| Lifecycle | none | `open → acknowledged → resolved` **[Existing]** | `open → acknowledged → resolved` (resolved final) **[Existing]** |
| Severity | as reported (`info`/`warning`/`critical`) | engine-assigned | max of its alerts |

Existing behaviour retained: an alert belongs to at most one incident (`alerts.incident_id`); grouping rule `same_asset_or_system_within_15_min` (`incident_engine.py`); incident status changes cascade to alerts. Note [Existing]: the dashboard KPI “Alerts raised” counts **events** with warning/critical severity (`dashboard_core_pg._alert_counts`); the canonical vocabulary calls that “events with severity” (see `CORE_DATA_ARCHITECTURE.md` F-10).

---

## 3. Entity relationships

![Entity relationships](./img/entity_relationships.png)

Diagram notes: `retention_policies`, `unit_conversions`, `metric_type_system_compat`, `event_type_system_compat` are catalogue/config tables without drawn relationships to keep the diagram readable. `building_id` columns marked “NEW column” are denormalised and kept consistent by composite FKs (§15). `asset_external_ids` has composite key `(source_id, external_id)`.

---

## 4. Building and location hierarchy

**Ownership & isolation model** [Proposed]

- `organizations (1) → buildings (n)`: supports several buildings per customer and several customers later. A single-customer install has one `organizations` row (`organization_code='DEFAULT'`), so no behaviour changes.
- Isolation unit = `building_id`. Every operational row carries it. Queries accept an optional building scope; permissions (future) are granted per building.
- **Sites/campuses** are **not** modelled as a separate table. A campus is represented by `locations` with `location_type='SITE'` under a building *or* by grouping buildings under an organization. **[Open Q5]**.
- **Relationship with the existing implementation:** `buildings` and `locations(building_id)` already exist; the KPI API already filters by `building_id` (`dashboard_core_pg.SCOPE_CTE`). The change is mostly *making the building explicit* and *extending the filter to the other endpoints*.

**`buildings` [Existing] + new columns**

| Column | Notes |
|---|---|
| `building_id` BIGINT PK | [Existing] |
| `name` TEXT UNIQUE | [Existing] (should become unique per organization **[Open]**) |
| `organization_id` BIGINT FK | **[NEW column]**, backfilled to the default organization |
| `building_code` TEXT UNIQUE | **[NEW column]** stable code used in the contract (e.g. `MAIN-TOWER`) |
| `timezone` TEXT | **[NEW column]** IANA name, validated against `pg_timezone_names`; replaces `LOCAL_UTC_OFFSET` **[Open Q2]** |
| `work_start_local`, `work_end_local` TIME | **[NEW column]** replace `WORK_START/WORK_END` (8/18) |

**`locations` [Existing] + new columns**

| Column | Notes |
|---|---|
| `location_id`, `building_id`, `floor`, `name` | [Existing]; UNIQUE `(building_id, name)` stays |
| `parent_location_id` BIGINT FK (self) | **[NEW column]** hierarchy: SITE → FLOOR → ZONE → ROOM; NULL = top level |
| `location_type` TEXT | **[NEW column]** `SITE\|FLOOR\|ZONE\|ROOM` (existing rows → `ZONE`) |
| `zone_type` TEXT, `is_sensitive` BOOLEAN | **[NEW column]** replace the string match `zone == "Server Room"` (`alert_engine.py`) |

Constraint: a child location must belong to the same building as its parent (composite FK `(parent_location_id, building_id) → locations(location_id, building_id)`).

---

## 5. Systems and assets

**`system_types` [Existing]** — canonical codes are the stored codes: `HVAC`, `NETWORKING`, `ACCESS_CONTROL`, `CCTV`, `FIRE_ALARM`. The `CHECK (code IN (...))` is to be dropped so a new system is an `INSERT`. UI/engine names (`NETWORK`, `ACCESS`) remain a presentation mapping at the API edge (AD-04).

**`assets` [Existing] + new columns**

| Column | Status | Description |
|---|---|---|
| `asset_id` | Existing | Internal ID (BIGINT identity). The only ID other tables reference. |
| `asset_code` | Existing | Stable human code, globally UNIQUE today (e.g. `HVAC-CRAC-01`). Used in the contract as the *internal* identifier. |
| `name`, `asset_type`, `system_type_id`, `location_id` | Existing | |
| `building_id` | NEW column | Denormalised; must equal the location’s building (composite FK). |
| `parent_asset_id` | NEW column | Parent-child equipment (e.g. fan coil → AHU), same building. Also usable later by incident correlation (`incident_engine.py` notes that no dependency table exists). |
| `manufacturer`, `model`, `serial_number` | NEW column | Descriptive only; **no logic may branch on them**. |
| `lifecycle_status` | NEW column | `PLANNED\|ACTIVE\|MAINTENANCE\|DECOMMISSIONED`. This is *installation/operational lifecycle*, not equipment state (§8). |
| `installed_at` | NEW column | |
| `metadata` JSONB | NEW column | Opaque descriptive data. |

**`asset_external_ids` [NEW entity]** — maps a vendor’s identifier to the internal asset.

| Column | Notes |
|---|---|
| `source_id` FK → `data_sources` | The vendor/connector namespace |
| `external_id` TEXT | Vendor asset/sensor identifier |
| `asset_id` FK → `assets` | |
| PK `(source_id, external_id)` | One vendor ID resolves to exactly one asset per source |

An asset may have several external IDs (several sources). The contract may identify an asset by `asset_code` **or** by `external_id` (resolved through the sending source).

---

## 6. Telemetry measurement contract (storage view)

`telemetry_records` [Existing] keeps `telemetry_id`, `asset_id`, `metric_id`, `ts`, `value` and `UNIQUE (asset_id, metric_id, ts)`.

| Column | Status | Description |
|---|---|---|
| `telemetry_id` | Existing | Measurement ID |
| `building_id` | NEW column | See §4 |
| `asset_id`, `metric_id` | Existing | |
| `ts` | Existing | **Observed time** (UTC) |
| `received_at` | NEW column | **Platform receive time.** NULL = unknown (legacy rows). Never back-filled with a guess. |
| `value` | Existing | `DOUBLE PRECISION NOT NULL`, finite (existing CHECK) |
| `source_id`, `batch_id` | NEW column | Provenance; legacy rows → source `legacy-seed` |
| `quality` | NEW column | `GOOD\|UNCERTAIN\|BAD`, default `GOOD` for legacy rows (§13) |
| `contract_version` | NEW column | e.g. `1.0` |
| `source_metadata` | NEW column | Optional JSONB: raw payload or reference to it |

**Unit normalization** — *connectors convert, Core validates.* The contract’s `unit` must equal `metric_types.unit` (the canonical unit) or a unit registered in `unit_conversions` for that metric (Core then converts and records the original in `source_metadata.original_value/original_unit`). Unknown unit → reject (`UNIT_MISMATCH`). **[Open Q1]** canonical unit for `bandwidth_util` (currently `%`; rules use Mbps).

**Timestamp normalization** — `observed_at` must be RFC 3339 UTC with `Z` (fractional seconds allowed). Offsets other than `Z` are rejected, not silently converted (the existing KPI parser, `dashboard_core_pg.parse_utc_timestamp`, applies the same strictness to query parameters). Future tolerance: `observed_at ≤ received_at + 5 min` (the rule already used by `POST /events`, `FUTURE_TOLERANCE`). Core never overwrites `observed_at`.

**Validation (in order):** schema → known building/source → asset resolution → metric known → metric allowed for the asset’s system (`metric_type_system_compat`) → unit → timestamp → finite value → plausible range (`min_plausible/max_plausible`; violation ⇒ stored as `BAD`, not rejected).

**Duplicate handling & idempotency** — natural key `(asset_id, metric_id, ts)` (existing UNIQUE):
- same key, same value → success, counted as `duplicate` (idempotent retry);
- same key, different value → **conflict**: first write wins, new record goes to `ingestion_rejections` with `reason_code='CONFLICT'` for review. **[Open]** whether a source may legitimately *correct* a value.

---

## 7. Engineering units

Canonical unit = `metric_types.unit` **[Existing]**. Current seed values [Verified]:

| metric (`name`) | canonical unit today | Note |
|---|---|---|
| `temperature` | `C` | engine labels it `°C` (presentation) |
| `humidity` | `%` | |
| `power_kw` | `kW` | |
| `cpu_load` | `%` | |
| `packet_loss` | `%` | |
| `bandwidth_util` | `%` | **conflicts with Mbps thresholds** (F-05) **[Open Q1]** |
| `smoke_level` | `%/m` | |
| `camera_fps` | `fps` | |

Proposed additions to `metric_types`: `value_kind` (`NUMERIC|BOOLEAN|COUNTER`), `min_plausible`, `max_plausible`. `unit_conversions(from_unit, to_unit, factor, offset)` initially holds only conversions needed by an actual connector (e.g. `F→C`: `(v−32)/1.8`); connectors remain the primary converters.

---

## 8. Equipment state model

Four *independent* dimensions plus lifecycle. All are about the **equipment/data path**, none is derived from “no data” alone.

| Dimension | Values | Derived from |
|---|---|---|
| `operational_state` | `NORMAL \| WARNING \| CRITICAL \| UNKNOWN` | Open fault conditions and event severities. Reuses the existing rule: critical = latest event of an opener/closer pair is the opener with severity critical, or an unpaired critical event in the window; warning = warning event(s) in the window; `UNKNOWN` = asset has never produced any telemetry/event. |
| `availability` | `AVAILABLE \| UNAVAILABLE \| UNKNOWN` | **Explicit evidence only**: an open availability opener (`camera_offline`, `link_down`) or a connector state report. Silence never sets `UNAVAILABLE`. |
| `communication_status` | `COMMUNICATING \| DEGRADED \| LOST \| UNKNOWN` | The *data path*: from freshness (§9) and the status of the asset’s `data_sources` row. `LOST` means the Core has stopped hearing from the device/source, **not** that the equipment is off. |
| `freshness` | `FRESH \| STALE \| MISSING \| NOT_APPLICABLE` | Age of last observation vs expected interval (§9). |
| `lifecycle_status` (on `assets`) | `PLANNED \| ACTIVE \| MAINTENANCE \| DECOMMISSIONED` | Provisioning; assets not `ACTIVE` are excluded from freshness alarms. |

**`asset_state_current`** [NEW entity] — one row per asset: the four dimensions above, `last_observed_at`, `last_received_at`, `reason_code`, `state_since`, `evaluated_at`, `evaluation_mode` (`LIVE|REPLAY`).
**`asset_state_history`** [NEW entity] — append-only; one row per **change** of `operational_state`, `availability` or `communication_status` (freshness transitions are recorded as `communication_status` changes; raw freshness is recomputed, not stored per tick). Each row carries `observed_at`, `received_at`, `source_id`, `reason_code`, and `cause_event_id` when an event caused it.

**Compatibility projection (preserves today’s KPI/UI):** the existing five health statuses are computed from these fields without changing results: `critical`→`operational_state=CRITICAL`; `no_data`→`freshness IN (STALE,MISSING)` while asset previously reported *and* no heartbeat/telemetry in the KPI window; `warning`→`WARNING`; `unknown`→`UNKNOWN`; `normal`→otherwise. The KPI SQL is **not** replaced initially (`DATA_MIGRATION_PLAN.md` M5).

---

## 9. Freshness and missing-data semantics

**`reporting_expectations`** [NEW entity]: `(asset_id, metric_id NULL=heartbeat/any, expected_interval_s, stale_factor, missing_factor, is_liveness)`; defaults per asset type/metric where no row exists. Initial values are taken from observed behaviour [Verified]: telemetry every **900 s** (seed), heartbeat every **3600 s** (`dashboard_core_pg._heartbeat_compliance`, seed). Event-only devices (door readers) have no telemetry expectation → `NOT_APPLICABLE` unless they send heartbeats.

**Initial rules** (defaults for discussion; all configurable):

- `age = evaluation_time − last_observed_at` (data age).
- `FRESH` if `age ≤ stale_factor × E` (default `stale_factor = 2`);
- `STALE` if `stale_factor × E < age ≤ missing_factor × E` (default `missing_factor = 5`);
- `MISSING` if `age > missing_factor × E`;
- `NOT_APPLICABLE` when no expectation exists or `lifecycle_status ≠ ACTIVE`; never `FRESH` without at least one observation (`MISSING`/`UNKNOWN` instead).
- **Series-level** freshness is evaluated per `(asset, metric)`; **asset-level** freshness uses the most recent liveness signal (any `is_liveness` series). An asset whose temperature series is stale but which still sends heartbeats is `COMMUNICATING` with one stale series.
- `evaluation_time` = wall clock in `LIVE` mode; an explicit `as_of` in `REPLAY` mode (keeps today’s data-relative demo behaviour, AD-07).
- If the asset’s `data_sources.status` is `ERROR/DISABLED`, `communication_status` becomes `UNKNOWN` with `reason_code='SOURCE_ERROR'` and **`operational_state` and `availability` are left unchanged**.

**Missing vs delayed vs irregular:** missing = no row within the window → `data_gaps` row; delayed = row exists but `received_at − ts` exceeds a per-source threshold (derived flag, not stored); irregular = observed spacing deviates from `E` (reported by the gap query, `HISTORIAN_DESIGN.md` §9).

---

## 10. Events, alerts, and incidents

**`event_records` [Existing] + new columns:** `building_id`, `received_at`, `source_id`, `source_event_id` (connector’s own event ID; dedupe key), `contract_version`. `severity` stays the *reported* severity (`info|warning|critical`). `details` JSONB unchanged (existing consumers read keys such as `reason`, `badge`/`card_id`, `seconds_open`, `port`).
`event_types` gains `event_class` (`ACTIVITY|FAULT_OPEN|FAULT_CLOSE|HEARTBEAT|STATE_REPORT`) and `resolves_event_type_id` (self-FK; e.g. `link_up → link_down`), consolidating the three hand-written pair lists (`dashboard_core_pg.FAULT_RECOVERY_PAIRS`, `integrity_rules.CLOSERS`, `v_camera_status`).
**System-generated events** (e.g. `state_changed`, `source_status_changed`) are ordinary `event_records` whose `source_id` points to the built-in source `core-engine` (`source_type='CORE'`).
Idempotency: existing UNIQUE `(asset_id, event_type_id, ts)` stays for legacy rows; connector events additionally use `UNIQUE (source_id, source_event_id) WHERE source_event_id IS NOT NULL` so distinct events in the same second are not collapsed (F-15).

**`alerts` [Existing] + new columns:** `building_id`; `detected_at` (processing time — today’s `detected_ts` is the first data timestamp of the detection, F-16); `acknowledged_ts`, `acknowledged_by`, `resolved_by`; `rule_version`. Threshold vs anomaly vs compound stays in `detection_method`. Add `UNIQUE (rule_id, asset_id, first_ts)` (F-14). Lifecycle remains `open → acknowledged → resolved` with `CHECK ((status='resolved') = (resolved_ts IS NOT NULL))` (existing).
**`incidents` [Existing] + new column** `building_id` (derived from `location_id`); lifecycle and grouping unchanged.

---

## 11. Evidence model

Existing mechanism [Verified]: `alerts.evidence` JSONB holds `{thresholds, facts, event_count, events:[{ref:"E1", source:"event|telemetry", record_id, ts, event_type, value, unit, details}]}`; `evidence.py` re-reads each row from `event_records`/`telemetry_records` and compares timestamp/value/event type.

**`alert_evidence` [NEW entity]** (normalised mirror, dual-written; JSON stays for compatibility):

| Column | Description |
|---|---|
| `evidence_id` PK, `alert_id` FK, `ref` (`E1…`) | Same refs as the JSON; UNIQUE `(alert_id, ref)` |
| `evidence_type` | `MEASUREMENT \| EVENT \| STATE_CHANGE` |
| exactly one of `telemetry_id`, `event_id`, `state_id` | FKs to the source row; `CHECK` exactly-one |
| `asset_id`, `building_id` | Copied from the source row; must match the alert’s asset/building |
| `observed_at`, `received_at` | Copied from the source row |
| `source_id` | Copied from the source row |
| `validation_status` | `UNVERIFIED \| VERIFIED \| MISMATCH \| SOURCE_MISSING` — maps to `evidence._verify` outcomes: none→`VERIFIED`; “row not found”→`SOURCE_MISSING`; timestamp/value/event-type differs→`MISMATCH` |
| `validated_at` | |
| `snapshot` JSONB | Verbatim copy of the evidence item at detection time |
| `metadata` JSONB | |

Evidence remains traceable to actual data: AI text is only ever checked **against** these rows (`validation.py` grounding checks) and is never itself an evidence source. Computed facts (`evidence.facts`, e.g. baseline median) stay in `alerts.evidence.facts`; they are not row-backed and are labelled as derived.

---

## 12. Data source model

**`data_sources`** [NEW entity]

| Column | Description |
|---|---|
| `source_id` PK, `source_code` UNIQUE | e.g. `legacy-seed`, `core-engine`, `vendor-a-bms-main-tower` |
| `building_id` FK | A connector serving two buildings registers two sources |
| `source_type` | `CONNECTOR \| SIMULATOR \| MANUAL \| API \| CORE` |
| `connector_name`, `connector_version`, `vendor`, `integration_name` | Identity (descriptive only) |
| `status` | `ACTIVE \| DEGRADED \| ERROR \| DISABLED` |
| `last_success_at`, `last_error_at`, `last_error` | Updated by ingestion |
| `source_metadata` JSONB | Opaque |

Seed rows to create during migration: `legacy-seed` (type `SIMULATOR`, for all existing facts) and `core-engine` (type `CORE`).
`ingestion_batches` and `ingestion_rejections` [NEW entities] record per-batch counts (`accepted/duplicate/rejected`, `client_batch_id` for retry idempotency) and rejected payloads (`reason_code`, `payload`), giving an audit trail without a message broker.

---

## 13. Data quality indicators

| Indicator | Meaning | Stored how | Used by |
|---|---|---|---|
| `GOOD` | Valid, plausible, trusted | `telemetry_records.quality` | KPI, alerts, charts |
| `UNCERTAIN` | Valid but source flagged low confidence (e.g. sensor warming up, substituted value) | `quality` | KPI/alerts **include** by default but the evidence marks it; configurable |
| `BAD` | Structurally valid but unusable (outside plausible range, connector-declared bad) | `quality` | Kept for audit; **excluded** from KPI/alerts via a compatibility view |
| `MISSING` | No value was obtained for an expected observation | **Not a telemetry row** (`value` stays NOT NULL). Contract may carry `quality: MISSING` (no `value`) → recorded in `data_gaps` with `cause='SOURCE_DECLARED_MISSING'` | freshness, gap reports |

How other conditions are represented:

| Condition | Representation |
|---|---|
| **Invalid** (schema, unknown asset/metric/unit, bad timestamp, non-finite value) | Rejected; row in `ingestion_rejections`; never in the Historian |
| **Implausible** (outside `min/max_plausible`) | Stored `BAD` |
| **Stale** | Derived `freshness=STALE` (not a property of a measurement) |
| **Missing** | Absence + `data_gaps` row; `freshness=MISSING` |
| **Delayed** | `received_at − ts > source delay threshold`; derived `is_late`; late rows can still be evaluated by the idempotent detection run |
| **Duplicate** | No-op, counted in `ingestion_batches.duplicate_count` |

Existing rows get `quality='GOOD'` so current KPI/alert results are unchanged.

---

## 14. Primary and foreign key strategy

- **Surrogate keys:** `BIGINT GENERATED … AS IDENTITY`, named `<entity>_id` (existing convention). Only surrogate keys are referenced by FKs.
- **Stable codes** (immutable once referenced): `organization_code`, `building_code`, `asset_code`, `source_code`, `metric_types.name`, `event_types.name`, `system_types.code`. These are what connectors use; they are resolved to surrogate keys at ingestion.
- **Natural keys for idempotency:** telemetry `(asset_id, metric_id, ts)`; events `(source_id, source_event_id)` plus legacy `(asset_id, event_type_id, ts)`; alerts `(rule_id, asset_id, first_ts)`; external IDs `(source_id, external_id)`.
- **Building consistency** via composite FKs: `locations(location_id, building_id)` and `assets(asset_id, building_id)` become UNIQUE targets; child tables reference `(asset_id, building_id)`.
- **Delete policy:** `ON DELETE RESTRICT` everywhere; retirement is `lifecycle_status='DECOMMISSIONED'`, not deletion.
- **Partition note:** if `telemetry_records` is later partitioned by `ts`, its PK and any referencing FKs must include `ts` (`HISTORIAN_DESIGN.md` §14); `alert_evidence.telemetry_id` is therefore designed to also store `telemetry_ts` so the FK can become `(telemetry_id, ts)`.

## 15. Required database constraints and indexes (specification)

| Table | Constraint / index | Reason |
|---|---|---|
| `buildings` | `UNIQUE (building_code)`; trigger/CHECK validating `timezone`; `CHECK (work_start_local < work_end_local)` | contract identity; replaces fixed UTC+2 |
| `locations` | `UNIQUE (location_id, building_id)`; FK `(parent_location_id, building_id)`; CHECK `location_type` | same-building hierarchy |
| `assets` | `UNIQUE (asset_id, building_id)`; FK `(location_id, building_id)`; FK `(parent_asset_id, building_id)`; `CHECK (parent_asset_id <> asset_id)`; CHECK `lifecycle_status` | building consistency, no self-parent |
| `asset_external_ids` | PK `(source_id, external_id)`; index `(asset_id)` | resolution |
| `data_sources` | `UNIQUE (source_code)`; CHECK `source_type`, `status`; index `(building_id)` | |
| `telemetry_records` | keep `UNIQUE (asset_id, metric_id, ts)`; FK `(asset_id, building_id)`; CHECK `quality`; `CHECK (received_at IS NULL OR ts <= received_at + interval '5 minutes')`; index `(building_id, ts)`; index `(source_id, received_at)` | idempotency, provenance, building analytics |
| `event_records` | keep both UNIQUEs; add partial `UNIQUE (source_id, source_event_id)`; FK `(asset_id, building_id)`; index `(building_id, ts)`, `(event_type_id, ts)` | F-15 |
| `event_types` | self-FK `resolves_event_type_id`; CHECK `event_class` | single fault/recovery definition |
| `reporting_expectations` | `UNIQUE (asset_id, metric_id)` (NULL metric handled by a partial unique index); `CHECK (expected_interval_s > 0)` | |
| `asset_state_current` | PK `asset_id`; CHECK enums; **CHECK: `freshness IN ('FRESH','NOT_APPLICABLE') OR operational_state` stays independent** (no silence-implies-state rule) | AD-06 |
| `asset_state_history` | index `(asset_id, observed_at DESC)`; append-only trigger | state history queries |
| `alerts` | `UNIQUE (rule_id, asset_id, first_ts)`; FK `(asset_id, building_id)`; index `(building_id, status, first_ts DESC)` | F-14 |
| `incidents` | index `(building_id, status, last_ts)` | |
| `alert_evidence` | `UNIQUE (alert_id, ref)`; exactly-one-source CHECK; FKs RESTRICT; index `(telemetry_id)`, `(event_id)` | referential evidence |
| `ingestion_batches` | `UNIQUE (source_id, client_batch_id)` | idempotent retries |

Append-only triggers (reject `UPDATE/DELETE`) on facts are **[Open]** — they conflict with any future retention job; deletion would run under a dedicated role/function.

---

## 16. JSON contract examples (contract version `1.0`) [Proposed]

These show the proposed standard. **The current implementation does not accept them.**

### 16.1 Batch envelope (common)

```json
{
  "contract_version": "1.0",
  "batch_id": "b5c1a6a0-3c5e-4e0e-9a1e-2b7d8c1f0a11",
  "sent_at": "2026-11-15T09:15:04Z",
  "source": {
    "source_code": "vendor-a-bms-main-tower",
    "connector": { "name": "vendor-a-connector", "version": "0.3.1" }
  },
  "building_code": "MAIN-TOWER",
  "records": [ "…one or more record objects below…" ]
}
```

### 16.2 Normalized telemetry measurement

Two different vendors, same canonical output. Vendor A’s raw payload (Fahrenheit, epoch seconds) and Vendor B’s raw payload (Celsius string unit, local offset) are normalized **by their connectors**:

Vendor A raw (illustrative): `{"sensor":"TS-114","temp_f":76.28,"ts":1794734100}`
Vendor B raw (illustrative): `{"deviceId":"B-77/ai3","value":24.6,"unit":"Celsius","time":"2026-11-15T11:15:00+02:00"}`

```json
{
  "record_type": "telemetry",
  "asset": { "external_id": "TS-114" },
  "metric_code": "temperature",
  "value": 24.6,
  "unit": "C",
  "observed_at": "2026-11-15T09:15:00Z",
  "quality": "GOOD",
  "source_metadata": { "original_value": 76.28, "original_unit": "F", "raw_ref": "vendor-a/2026-11-15/0915" }
}
```

```json
{
  "record_type": "telemetry",
  "asset": { "external_id": "B-77/ai3" },
  "metric_code": "temperature",
  "value": 24.6,
  "unit": "C",
  "observed_at": "2026-11-15T09:15:00Z",
  "quality": "GOOD",
  "source_metadata": { "original_unit": "Celsius", "original_time": "2026-11-15T11:15:00+02:00" }
}
```

### 16.3 Equipment state update

```json
{
  "record_type": "state",
  "asset": { "asset_code": "CCTV-CAM-02" },
  "observed_at": "2026-11-15T09:20:11Z",
  "availability": "UNAVAILABLE",
  "communication_status": "COMMUNICATING",
  "reason_code": "VIDEO_SIGNAL_LOST",
  "message": "no video signal",
  "source_metadata": { "vendor_state": "NO_SIGNAL" }
}
```

### 16.4 Operational event

```json
{
  "record_type": "event",
  "source_event_id": "evt-000184233",
  "asset": { "asset_code": "ACS-RDR-02" },
  "event_type": "door_forced",
  "observed_at": "2026-11-15T09:21:47Z",
  "severity": "CRITICAL",
  "details": { "note": "door forced" }
}
```

### 16.5 Batch response (proposed)

```json
{
  "batch_id": "b5c1a6a0-3c5e-4e0e-9a1e-2b7d8c1f0a11",
  "received_at": "2026-11-15T09:15:05Z",
  "accepted": 1,
  "duplicates": 0,
  "rejected": [ { "index": 1, "reason_code": "UNIT_MISMATCH", "message": "unit 'F' is not allowed for metric 'temperature'" } ]
}
```

### 16.6 Field requirements

| Field | Telemetry | State | Event | Notes |
|---|---|---|---|---|
| `contract_version` (envelope) | **required** | required | required | `MAJOR.MINOR` |
| `source.source_code` | required | required | required | must exist in `data_sources` and match the authenticated sender (auth is **[Open]**; none exists today) |
| `building_code` | required | required | required | |
| `batch_id` | required (UUID) | required | required | retry idempotency |
| `record_type` | required | required | required | `telemetry\|state\|event` |
| `asset.asset_code` **or** `asset.external_id` | exactly one | exactly one | exactly one | conditional |
| `metric_code` | required | — | — | |
| `value` | required unless `quality=MISSING` | — | optional | finite number |
| `unit` | required with `value` | — | optional | must be canonical/registered |
| `observed_at` | required | required | required | UTC `Z` |
| `quality` | optional (default `GOOD`) | — | — | `GOOD\|UNCERTAIN\|BAD\|MISSING` |
| `event_type` | — | — | required | in `event_types`, valid for the asset’s system |
| `severity` | — | — | optional (default `INFO`) | `INFO\|WARNING\|CRITICAL` (uppercase in contract; stored lowercase as today) |
| `source_event_id` | — | — | **required for connectors** (recommended) | dedupe key |
| `operational_state`, `availability`, `communication_status` | — | at least one required | — | enums §8 |
| `reason_code`, `message`, `details`, `source_metadata` | optional | optional | optional | opaque JSON objects; size-limited **[Open]** |

`received_at` is **never** supplied by the sender; the Core stamps it.

---

## 17. Versioning and compatibility strategy

- **Contract version** `MAJOR.MINOR` in every envelope and stored per row (`contract_version`). MINOR = additive optional fields; MAJOR = breaking. The Core accepts the current and previous MAJOR during a published deprecation window (length **[Open]**).
- **Unknown fields:** ignored with a warning in the response; only `source_metadata`/`details` content is persisted opaque. Unknown enum values → reject.
- **Schema evolution** is by numbered migration files after `004` (proposed `005…`), tracked in a `schema_migrations` table (F-20); init scripts remain loadable for fresh databases (`day2_ahmed/test_day2_integration.py` rebuilds from schema file + `003`).
- **Backward compatibility rules:** existing tables/columns are never renamed or dropped in Sprint 1; compatibility views (`v_telemetry_canonical`, `v_event_canonical`, and building-aware versions of the `v_*` chat views) expose old column names and semantics; legacy rows keep `source='legacy-seed'`, `quality='GOOD'`, `received_at=NULL`.
- **API compatibility:** existing endpoints and response shapes (`/dashboard/*`, `/alerts`, `/incidents`, `/chat`, `/ask`, `POST /events`, `POST /alerts`) are unchanged; ingestion is a new route (name **[Open]**; not `/api/v1` unless Amr confirms the platform convention).
- **Terminology for UI vs DB** stays a translation layer (AD-04).
