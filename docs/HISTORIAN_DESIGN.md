# HISTORIAN_DESIGN.md — Anzma AI Insight Core

| | |
|---|---|
| **Status** | Historian design plus implemented ingestion and freshness schema. Migrations 006–008 add ingestion provenance/state facts; migration 009 adds expectation, gap, and current-freshness storage plus building schedules. The freshness evaluator, derived state, retention, and rollups remain future work. |
| **Baseline** | PostgreSQL 16; migrations 006–009 are included for fresh volumes and can be applied additively to existing volumes with the documented runner. The shared database was not migrated. |
| **Related** | `CANONICAL_DATA_MODEL.md` (entities/columns named here), `DATA_MIGRATION_PLAN.md`, `SPRINT_1_TECHNICAL_ISSUES.md` |

Entities marked *(proposed)* are defined in `CANONICAL_DATA_MODEL.md`. SQL below is **illustrative query design**, not a migration; column names that do not exist today are marked *(proposed)*.

---

## 1. Historian objectives

1. Keep an **independent, durable historical record** of measurements, equipment state changes and operational events, separate from any vendor system.
2. Preserve **observed time** and **received time**, **source**, **quality** and **building** for every record.
3. Answer historical questions (latest value, ranges, aggregates, state/alert timelines) for **multiple buildings and assets**.
4. Make **missing, late, stale and duplicate data explicit** rather than silent.
5. Be the single data source the existing intelligence reads through (KPI, alert engine, incident engine, evidence, AI, chat) without changing their behaviour.
6. Stay on **PostgreSQL**; add nothing that a measured requirement does not justify.

What exists today [Verified]: `POST /api/v1/ingest/batches` writes telemetry, events, and append-only equipment state with source, observed/received time, quality, and batch provenance. `v_telemetry_valid` excludes `BAD` while retaining legacy `NULL` quality. Rejected records are audited separately. Freshness gap detection, retention jobs, rollups, and a repository backup runbook are not implemented. The existing legacy endpoints remain supported.

---

## 2. Responsibilities and boundaries

| Responsibility | Owner |
|---|---|
| Vendor protocol, polling/subscription, vendor→canonical mapping, unit conversion, UTC timestamps, store-and-forward, retry with the same IDs | **Connector / plugin** (outside the Core) |
| Contract validation, identity resolution, compatibility checks, idempotent persistence, rejection audit | **Core – ingestion service** |
| Storage, retention, historical query API, aggregates | **Core – Historian** |
| State derivation, freshness, gap detection | **Core – state/freshness job** |
| KPI, alerts, incidents, evidence, AI, dashboards | **Core – operational intelligence** (existing; read through the repository/view layer) |
| Authentication of connectors | **Core** (implemented for ingestion with per-source hashed keys; operational REST/Socket.IO use the separate deployment bearer token; user-specific identity/RBAC remains open) |

The Historian is **not** a separate service or database in Day 1: it is a set of PostgreSQL tables, one ingestion module, one query/repository module and one scheduled job, all inside the existing Core deployment.

---

## 3. Data acquisition flow

![Data acquisition flow](./img/Data_acquisition_flow.png)

Step contract (Core side):

1. **Receive** a batch; stamp `received_at` = platform clock; create an `ingestion_batches` row keyed by `(source_id, batch_id)` — a replayed batch returns the stored result.
2. **Validate** each record (order in `INGESTION_CONTRACT.md` §10): schema/scope → asset permission → registered and compatible metric/event → unit → timestamp (UTC `Z`, future tolerance and per-source maximum back-date) → value and plausibility.
3. **Persist** accepted records in one transaction per batch using `INSERT … ON CONFLICT DO NOTHING`; classify each outcome `accepted | duplicate | rejected`.
4. **Record** rejections (`reason_code`, payload) in `ingestion_rejections`.
5. **Update** `data_sources.last_success_at` / `last_error_*`.
6. **Return** per-record results. Accepted facts, batch response, and rejection audit commit in one transaction; `200 PARTIAL` and `422 REJECTED` both state that the batch transaction committed.

Existing path kept during transition: seed SQL and `POST /events` continue to write to the same tables; legacy fact rows remain source-less (`source_id IS NULL`) unless explicitly provisioned later.

---

## 4. PostgreSQL storage design

**Decision: keep the existing PostgreSQL tables as the Historian.** No TimescaleDB, Kafka, Redis or second database (AD-01).

| Data | Table | Nature | Notes |
|---|---|---|---|
| Time-series telemetry | `telemetry_records` [Existing] | Append-mostly fact | + `building_id`, `received_at`, `source_id`, `batch_id`, `quality`, `contract_version`, `source_metadata` *(proposed columns)* |
| Operational events | `event_records` [Existing] | Immutable fact | + `building_id`, `received_at`, `source_id`, `source_event_id` *(proposed)* |
| Current equipment state | `asset_state_current` *(proposed)* | One row per asset, overwritten | derived |
| Equipment state history | `asset_state_history` *(proposed)* | Append-only transitions | |
| Alert history | `alerts` [Existing] | Detection + lifecycle | rows are never deleted; status changes are timestamped (`acknowledged_ts`, `resolved_ts`) |
| Incident history | `incidents` [Existing] | Case + lifecycle | |
| Evidence references | `alert_evidence` *(proposed)* + `alerts.evidence` JSONB [Existing] | FK-backed links to fact rows | dual-write |
| Source health & batches | `data_sources`, `ingestion_batches`, `ingestion_rejections` *(proposed)* | Operational audit | |

Row-shape sanity check [Verified from the seed script]: 15 telemetry series at 15-minute cadence for 78 h ≈ 4.7 k telemetry rows and ≈ 950 hourly heartbeat rows plus 15 incident events. A single 15-minute series produces 96 rows/day; **10 000 such series ≈ 1 M rows/day** (arithmetic, not a measured load). Plain PostgreSQL with the existing indexes handles this class of volume; no storage change is justified now.

---

## 5. Time-series data organization

- **Series identity** = `(asset_id, metric_id)`; one sample per `ts` (existing `UNIQUE (asset_id, metric_id, ts)`, which also serves “latest value” and range scans as a backward/forward index scan).
- **Building-level scans** use `(building_id, ts)` *(proposed index)*.
- **Provenance index** `(source_id, received_at)` *(proposed)* for batch reconciliation.
- **Time zone:** all `TIMESTAMPTZ`, sessions set to UTC (as `dashboard_core_pg.open_readonly` does). Local-time reasoning (working hours) uses `buildings.timezone` *(proposed)*.
- **Ordering semantics:** the Historian orders by observed time `ts`; `received_at` is used only for latency, lateness and reconciliation.
- **Late and out-of-order data** are accepted (subject to the future-time rule); they never rewrite existing samples. The detection run is idempotent (`store_alerts` + planned unique index), so late data can raise missing alerts on the next run.
- **Quality:** queries for analytics read through `v_telemetry_valid` *(proposed view: `quality IN ('GOOD','UNCERTAIN')`)*, so `BAD` samples are retained but excluded — identical results today because every existing row is `GOOD`.

---

## 6. State history and current state

- **Current state:** `asset_state_current` holds the latest `operational_state`, `availability`, `communication_status`, `freshness`, `last_observed_at`, `last_received_at`, `evaluated_at`.
- **History:** `asset_state_history` receives a row **only when** `operational_state`, `availability` or `communication_status` changes, with `observed_at`, `received_at`, `source_id`, `reason_code`, and `cause_event_id`.
- **Sources of change:** (a) connector `state` records (ingestion); (b) derivation from events using the opener/closer relation (`event_types.resolves_event_type_id`, which replaces the three duplicated pair lists); (c) the freshness job (communication status only).
- **Rule:** missing telemetry changes `freshness`/`communication_status`, **never** `availability` or `operational_state` (AD-06).
- **Rebuildability:** `asset_state_current` and `asset_state_history` entries derived from events can be recomputed from `event_records` + `telemetry_records`; a verification query compares recomputed and stored states (§13).

---

## 7. Historical query interfaces

Read interface (Python repository, proposed; the SQL shows intent). All functions take an optional `building_id` (NULL = all permitted buildings), UTC `start`/`end` (`[start, end)`), and return observed timestamps in UTC.

```python
class HistorianRepository:                                   # proposed interface
    def latest_measurements(self, *, building_id=None, asset_ids=None, metric=None, as_of=None): ...
    def measurements(self, *, asset_id, metric, start, end, quality="valid", limit=10_000): ...
    def aggregate(self, *, building_id=None, asset_ids=None, system=None, metric, start, end,
                  bucket="hour", functions=("min", "max", "avg", "count")): ...
    def state_history(self, *, asset_id, start, end): ...
    def alert_timeline(self, *, building_id=None, asset_id=None, incident_id=None, start, end): ...
    def building_analytics(self, *, building_id, system=None, metric, start, end, bucket="hour"): ...
    def gaps(self, *, building_id=None, asset_ids=None, start, end): ...
    def series_freshness(self, *, building_id=None, evaluation_time=None): ...
```

Limits protect the database: `limit`/max range per call (the dashboard already caps ranges: 31 days in `dashboard_core_pg.MAX_RANGE_DAYS`, 168 h in `frontend_api.MAX_HOURS`).

### Query patterns

**Latest measurement per asset and metric** (uses the existing unique index):

```sql
SELECT DISTINCT ON (t.asset_id, t.metric_id)
       t.asset_id, t.metric_id, t.ts, t.value, t.received_at /* proposed */, t.quality /* proposed */
FROM telemetry_records t
WHERE (%(building)s::bigint IS NULL OR t.building_id = %(building)s)           -- building_id proposed
  AND t.ts <= %(as_of)s
ORDER BY t.asset_id, t.metric_id, t.ts DESC;
```

**Historical range for one series:**

```sql
SELECT ts, value, quality
FROM telemetry_records
WHERE asset_id = %(asset)s AND metric_id = %(metric)s AND ts >= %(start)s AND ts < %(end)s
ORDER BY ts;
```

**Aggregation by minute/hour/day** (PostgreSQL 16 `date_bin`; day buckets use the building time zone when `local=true`):

```sql
SELECT date_bin(%(bucket)s::interval, t.ts, TIMESTAMPTZ '2000-01-01') AS bucket_start,
       min(t.value), max(t.value), avg(t.value), count(*)
FROM telemetry_records t
JOIN assets a USING (asset_id)
WHERE t.metric_id = %(metric)s AND t.ts >= %(start)s AND t.ts < %(end)s
  AND (%(system)s::text IS NULL OR a.system_type_id = (SELECT system_type_id FROM system_types WHERE code = %(system)s))
  AND (%(building)s::bigint IS NULL OR a.building_id = %(building)s)           -- assets.building_id proposed
GROUP BY 1 ORDER BY 1;
```

This generalises what `frontend_api.trends` already does with `floor(extract(epoch …)/size)` bucketing (15-minute minimum bucket); results must match for the same inputs (parity test).

**Historical equipment state** (what was the state at time T):

```sql
SELECT DISTINCT ON (asset_id) asset_id, operational_state, availability, communication_status, observed_at
FROM asset_state_history                                                         -- proposed
WHERE observed_at <= %(t)s AND (%(building)s::bigint IS NULL OR building_id = %(building)s)
ORDER BY asset_id, observed_at DESC;
```

**Alert and incident timelines** (existing tables; `building_id` proposed):

```sql
SELECT 'alert' AS kind, alert_id AS id, first_ts AS ts, severity, status, title FROM alerts WHERE ...
UNION ALL
SELECT 'incident', incident_id, first_ts, severity, status, title FROM incidents WHERE ...
UNION ALL
SELECT 'event', event_id, ts, severity, 'n/a', et.name FROM event_records JOIN event_types et USING (event_type_id) WHERE severity <> 'info' AND ...
ORDER BY ts;
```

**Building-level / system-level analytics** are `aggregate()` with `building_id` and/or `system` and the same bucket logic; “now”-relative windows use the **building’s** data clock (per-building `as_of`) in REPLAY mode and wall clock in LIVE mode.

---

## 8. Aggregation strategy

1. **Phase A (now): on-demand aggregation** over raw rows with the indexes above. This is how every KPI and trend works today.
2. **Phase B (only when measured need appears): rollup tables** `telemetry_rollup_1h` and `telemetry_rollup_1d` *(future)* with columns `(asset_id, metric_id, bucket_start, min, max, avg, count, count_bad)` and the same `building_id`/quality semantics; populated by an idempotent job that recomputes closed buckets (and re-opens a bucket when late data arrives). Plain tables, not materialized views, so incremental refresh and retention are controllable.
3. **Trigger criteria for Phase B** (to be agreed, not decided here): a documented target for dashboard query latency at the expected series count/range, and a measured violation on realistic data. No fixed numbers are proposed in this document.
4. **Correctness rule:** aggregates are computed from `quality IN ('GOOD','UNCERTAIN')` rows; `count` and `count_bad` are both kept so data-quality is visible.
5. **KPI definitions are not changed** by aggregation work (`dashboard_core_pg.KPI_DEFINITIONS` remain the specification).

---

## 9. Missing-data detection

Inputs: `reporting_expectations.expected_interval_seconds` (E) per resolved series, `received_at`, `ts`. Migration 009 creates the expectation and gap tables; no freshness job writes them yet.

| Condition | Detection | Output |
|---|---|---|
| **Missing expected measurement** | For an expected series, `age > missing_multiplier × E` | On reaching `MISSING`, open `data_gaps` with `gap_start=last_observed_at`; close at the first new observation |
| **Stale telemetry** | `stale_multiplier × E < age ≤ missing_multiplier × E` | `freshness=STALE`; no gap yet |
| **Delayed ingestion** | `received_at − ts` above a per-source threshold | derived `is_late`, per-source latency stats; no state change |
| **Communication failure** | all liveness series of an asset (or the whole source) beyond `missing_factor × E`, or `data_sources.status` in (`ERROR`,`DISABLED`) | `communication_status=LOST` (or `UNKNOWN` with `SOURCE_ERROR`); **`availability` unchanged** |
| **Irregular sampling** | spacing coefficient of variation or share of intervals outside `[0.5E, 1.5E]` over a window | reported by the gap query; informational |
| **Missing observation** | connector omits the record; v1 rejects `quality=MISSING` | Migration 009 adds storage; the future evaluator writes gaps. Backfill gap splitting is deferred. |

Illustrative gap query (history only; a production evaluator must resolve the contract's expectation-scope precedence):

```sql
WITH s AS (
  SELECT tr.asset_id, tr.source_id, mt.name AS metric_code, tr.ts,
         lag(tr.ts) OVER (PARTITION BY tr.source_id,tr.asset_id,tr.metric_id ORDER BY tr.ts) AS prev_ts
  FROM telemetry_records tr JOIN metric_types mt USING (metric_id)
  WHERE tr.ts >= %(start)s AND tr.ts < %(end)s)
SELECT s.asset_id, s.metric_code, s.prev_ts AS gap_start, s.ts AS gap_end
FROM s JOIN reporting_expectations e
  ON e.scope='ASSET_METRIC' AND e.asset_id=s.asset_id AND e.record_kind='telemetry'
 AND e.metric_code=s.metric_code AND e.valid_from <= s.ts AND (e.valid_to IS NULL OR e.valid_to > s.ts)
WHERE s.ts - s.prev_ts > make_interval(secs => e.expected_interval_seconds * e.missing_multiplier);
```

Compatibility note: today’s `no_data` health status (KPI) and `heartbeat_compliance` (hourly assumption) remain computed as they are; the new detection adds information, it does not rewrite those KPIs. The seed’s Camera 2 outages (silent 45–44 h ago and the last 5 h) are the natural regression scenario: they must still produce `no_data` in the KPI and now additionally `freshness=MISSING` with `availability` driven only by the `camera_offline` events.

---

## 10. Data retention

**Framework** — policy is *data*, not code; **no retention period is decided in this document**. Default after migration: **retain everything**, retention job disabled.

`retention_policies` *(proposed)*: `(policy_id, data_class, building_id NULL, keep_for interval NULL=forever, action ENUM(DELETE|ROLLUP_THEN_DELETE|ARCHIVE), enabled, approved_by, approved_at)`.

| Data class | Policy dimensions to decide | Constraint |
|---|---|---|
| Raw measurements (`telemetry_records`) | keep duration; whether rollups must exist first | Cannot delete rows that `alert_evidence` still references while the alert is retained |
| Aggregates (`telemetry_rollup_*`) | keep duration (typically ≥ raw) | |
| Operational events | keep duration | Same evidence constraint |
| Alerts and incidents | keep duration | Prefer long/indefinite: they are the audit record |
| Evidence references (`alert_evidence`, `alerts.evidence`) | tied to the alert | A snapshot (`alert_evidence.snapshot`) keeps meaning after raw data expires; `validation_status` then becomes `SOURCE_MISSING` by *policy*, not by corruption — the evidence check must distinguish “expired by policy” from “missing” (add `expired_by_policy` reason) |
| Rejections, batches | keep duration | |

**Separation of policy and implementation:** a scheduled job reads enabled policies and executes `DELETE … WHERE ts < now() − keep_for` in bounded batches (later `DROP PARTITION` if partitioning exists). Deletion runs under a dedicated role; append-only triggers (if adopted) exempt that role. Every run writes a summary row (counts, range) for audit. Approval of actual durations is **[Open]** (business/compliance decision).

---

## 11. Buffering and retries

| Concern | Initial (PostgreSQL-only) | Future, only if justified |
|---|---|---|
| Temporary buffering | **Connector-side** store-and-forward is required of connectors. Core needs no broker: ingestion writes straight to PostgreSQL. | Core-side `ingestion_inbox` staging table (raw batch stored first, processed asynchronously) if bursts exceed synchronous capacity |
| Retry policy | Connectors retry with the **same `batch_id`** and exponential backoff; the Core is idempotent so retries are safe | Server-provided `Retry-After`, rate limits per source |
| Failure recovery | Ingestion transaction is per batch: all-or-nothing for *accepted* rows; a crash mid-request leaves no partial batch; the connector retries | Reprocess the inbox after crash |
| Dead-letter handling | `ingestion_rejections` (reason + payload) is the minimal dead-letter store, with a replay tool that re-submits fixed records | A broker DLQ — **not justified** at this scale |
| Back-pressure | HTTP 429/503 with `Retry-After` when the DB is unavailable (`db_adapter.DatabaseUnavailable` already maps to 503) | Queue |

Design requirement: ingestion must never block the intelligence loop; the detection loop (`app/main.py::_alert_loop`) must tolerate ingestion load (separate connections; statement timeouts as in `app/api/chat_lite.py`).

---

## 12. Idempotency

| Record | Idempotency key | Behaviour |
|---|---|---|
| Batch | `(source_id, batch_id)` (`ingestion_batches`) | Replay returns the original result; no side effects |
| Telemetry | `(asset_id, metric_id, ts)` (existing UNIQUE) | same value → `duplicate`; different value → `CONFLICT` rejection, first write kept (**[Open]**: allow sanctioned corrections?) |
| Event | `(source_id, source_event_id)` (new partial UNIQUE) else legacy `(asset_id, event_type_id, ts)` | `duplicate` |
| State report | `(asset_id, observed_at, source_id)` | `duplicate` |
| Alert | `(rule_id, asset_id, first_ts)` (new UNIQUE; replaces `WHERE NOT EXISTS`) | `INSERT … ON CONFLICT DO NOTHING`; detection loop guarded by `pg_advisory_lock` so multiple API workers cannot interleave |
| Incident assignment | alert has `incident_id` or not (existing) | unchanged |

Sub-second timestamps: the contract allows fractional seconds, so retried records must reproduce `observed_at` exactly (the connector’s responsibility).

---

## 13. Backup and recovery

Found in the repository: **no backup, restore or integrity-verification procedure**; PostgreSQL data lives in the named volume `pgdata`; init scripts run only on an empty volume. Proposed:

| Item | Initial | Notes |
|---|---|---|
| Backup | Scheduled `pg_dump` (custom format) of the `anzma` database to storage outside the Docker host volume; retention of backups set by operations | Simple, restorable, version-tolerant |
| Point-in-time recovery | WAL archiving + base backups **when** RPO requirements are defined | Not assumed needed; **[Open]** RPO/RTO |
| Restore procedure | Documented runbook: restore into a **scratch database**, run integrity checks, then swap | Also the basis of migration rehearsal (`DATA_MIGRATION_PLAN.md`) |
| Before every migration | Mandatory `pg_dump` + row-count/checksum snapshot | |
| Integrity verification | Reuse existing checks: `integrity_rules.stored_violations()` (compat violations), `evidence.alert_evidence()` (verified/unverified counts); add: orphan checks (FK sanity), building-consistency, gap/duplicate counts, per-table count + hash of `(asset_id, metric_id, ts, value)` ordered rows | Run after restore and nightly (report only) |
| State rebuild | Recompute `asset_state_*` from facts and diff against stored | |
| Backup secrecy | Dumps contain operational data; protect like the database. No credentials in documentation or repo (`.env` stays untracked) | |

---

## 14. Scalability considerations

Initial implementation is PostgreSQL only. Options are listed with the **trigger that would justify them**; none is recommended now.

| Option | When justified | Cost / constraint |
|---|---|---|
| Native range **partitioning** of `telemetry_records` by `ts` (e.g., monthly) | Table size makes retention deletes, vacuum or index maintenance a measured problem | In PostgreSQL, a partitioned table’s PK/UNIQUE must include the partition key: `UNIQUE (asset_id, metric_id, ts)` already does; `telemetry_id` PK would become `(telemetry_id, ts)`, so FKs referencing it (planned `alert_evidence.telemetry_id`) must also carry `ts` — hence the proposed `telemetry_ts` column in `alert_evidence`. Do this migration before volume makes it expensive. |
| **Rollup tables** | §8 criteria | Extra job + late-data handling |
| Read replica | Reporting load interferes with ingestion | Operational complexity |
| TimescaleDB / columnar store | Proven need for compression or continuous aggregates that PostgreSQL + rollups cannot meet | New dependency; **not introduced automatically** |
| Message broker (Kafka etc.) | Sustained ingest beyond synchronous PostgreSQL capacity, or multiple independent consumers | New infrastructure; not justified by current scale |
| Redis cache | Measured repeated-query latency | Not justified |
| Connection pooling (pgbouncer / psycopg pool) | Many API workers/connectors | Today each request opens a connection (F-21); consolidating the three connection factories is a prerequisite and a cheap first step |

---

## 15. Initial implementation phases

Phases are sequencing only — **no dates or owners are assigned**.

| Phase | Scope | Exit criteria |
|---|---|---|
| H0 — Baseline | Golden-output capture of KPI/alerts/incidents/evidence on a copy of the database; backup/restore rehearsal | Baseline stored; restore verified |
| H1 — Foundation schema | Additive columns/tables (`building_id`, provenance, `data_sources`, `ingestion_*`, `reporting_expectations`, `data_gaps`, `series_freshness_current`); building schedule parity backfill; compatibility views; migration runner | Existing tests + parity checks green; no behavior change |
| H2 — Ingestion service | Implemented by migrations 006–008 and `/api/v1/ingest/batches`; legacy `POST /events` remains | Conformance vectors and regression evidence are in `INGESTION_CONTRACT.md` and `PRODUCTIZATION_IMPLEMENTATION_REPORT.md` |
| H3 — State & freshness | Future `asset_state_current/history` and evaluator using the migration-009 gap/current tables | Camera-2 scenario verified; KPI output identical; no freshness alerts/incidents |
| H4 — Historical query layer | `HistorianRepository` + building-aware views; endpoints reuse it; parity with `frontend_api.trends` | Parity tests |
| H5 — Evidence & alert hardening | `alert_evidence` dual-write; unique alert index; advisory lock | Evidence verification unchanged (`test_day2_integration.py`) |
| H6 — Retention & operations | `retention_policies` (disabled by default), integrity-check job, backup schedule | Runbook reviewed |
| Later (measured need only) | Rollups, partitioning, pooling | §14 triggers |

Corresponding backlog items: `SPRINT_1_TECHNICAL_ISSUES.md`.
