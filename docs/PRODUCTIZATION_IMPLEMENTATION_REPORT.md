# Productization implementation report

**Review date:** 19 November 2026
**Checkout:** `feature/ingestion-foundation`; code implementation at `a9de778cf0793a530ae6e347480f07820c17ab7d`
**Scope:** Core only. No vendor connector or Milestone work.

## Phase 0 completion summary

| Item | Status | Evidence file |
|---|---|---|
| 0.1 — disposable baseline and regression capture | PASS | [`DAY3_FUNCTIONAL_TEST_REPORT.md`](DAY3_FUNCTIONAL_TEST_REPORT.md) |
| 0.2 — order-independent CRAC equipment-health regression | PASS | [`DAY3_FUNCTIONAL_TEST_REPORT.md`](DAY3_FUNCTIONAL_TEST_REPORT.md), [`DAY3_MIGRATION_REHEARSAL.md`](DAY3_MIGRATION_REHEARSAL.md) |
| 0.3 — event source identity migration | PASS | [`DAY3_MIGRATION_REHEARSAL.md`](DAY3_MIGRATION_REHEARSAL.md) |
| 0.4 — ingestion code aligned to contract | PASS | [`INGESTION_CONTRACT.md`](INGESTION_CONTRACT.md), [`DAY3_FUNCTIONAL_TEST_REPORT.md`](DAY3_FUNCTIONAL_TEST_REPORT.md) |
| 0.5 — H0 restore/migration and API rehearsal | PASS | [`DAY3_MIGRATION_REHEARSAL.md`](DAY3_MIGRATION_REHEARSAL.md) |
| 0.6 — all 16 conformance vectors | PASS | [`DAY3_FUNCTIONAL_TEST_REPORT.md`](DAY3_FUNCTIONAL_TEST_REPORT.md), [`INGESTION_CONTRACT.md`](INGESTION_CONTRACT.md) §13.3 |
| 0.7 — evidence reports and clean-clone deployment verification | PASS with release limitations recorded | [`DAY3_DEPLOYMENT_VERIFICATION.md`](DAY3_DEPLOYMENT_VERIFICATION.md), [`DAY3_DATA_VALIDATION.md`](DAY3_DATA_VALIDATION.md), [`KNOWN_ISSUES.md`](../KNOWN_ISSUES.md), [`RELEASE_CHECKLIST_v0.1.md`](RELEASE_CHECKLIST_v0.1.md) |

Phase 0 item statuses do not authorize a live migration or release tag. Live/shared migration remains untested; release gates and current risks are in the checklist and known-issues file.

## Executive report

The current application is a single-process FastAPI/Socket.IO service backed by PostgreSQL 16. Its active operational path is `app/main.py` → `app/services/pipeline.py` → domain alert/incident/evidence/AI modules → `app/adapters/database/db_adapter.py`; dashboard queries are in `app/repositories/dashboard_core_pg.py` and `app/api/frontend_api.py`. Chat routes and compatibility views are active. The old modules under `legacy/` are not the canonical runtime path.

The previously deployed schema is the base schema followed by migrations 003–005. Additive migrations 006–008 define the source registry and credentials, source-scoped mappings, provenance/quality, batches/rejections, append-only state facts, event identity, back-date policy, delivery diagnostics, and plausibility bounds. The application migration runner upgrades an existing database only when explicitly invoked; Compose init mounts include 006–008 only for a fresh database volume. The prior read-only query confirmed the shared runtime had 13 base tables; migrations 006–008 have not been applied to the shared database.

Existing stored telemetry and events already form a basic historical record. Multiple buildings, locations, systems, and assets are represented. The compatible ingestion surface now includes versioned `POST /api/v1/ingest/batches` alongside the existing single-record `POST /events` and manual alert `POST /alerts` routes; the v1 batch contract and its additive schema are documented below. Operational processing continues from stored facts into alert and anomaly detection, severity classification, incident correlation/lifecycle, evidence re-verification, grounding-only AI explanations, playbook recommendations, dashboard queries, and chat/tool routing.

**Approved implementation defaults (user approved on 18 November 2026):** versioned `POST /api/v1/ingest/batches`; per-record results committed atomically with their batch audit; external asset IDs unique within one data source and constrained to that source's building; rotatable/revocable per-source API keys stored only as hashes; and immutable first-write-wins telemetry conflict handling with rejected payloads retained for audit. No other architecture change is authorized by this approval.

## Approved request/response and security decisions

- Requests use contract `1.0`, a UUID `batch_id`, sender `sent_at`, registered `source_code`, connector name/version, registered `building_code`, and up to 5000 canonical telemetry/event/state records.
- Each record receives an explicit `ACCEPTED`, `DUPLICATE`, or `REJECTED` result. `200` represents accepted/duplicate outcomes and partial record rejection; `422` means all supplied records were rejected, **but the response and rejection audit were committed**. A transaction error returns retryable `503` and commits no batch facts/audit.
- `(source_id, batch_id)` plus a SHA-256 of canonical JSON distinguishes an exact retry from reuse with a changed body. A changed body returns `409 BATCH_ID_REUSED`. Telemetry natural key remains `(asset_id, metric_id, observed_at)`; equal value (within the documented `1e-9` relative tolerance) and quality is a duplicate, differing value or quality is a `CONFLICT`. Existing telemetry is never updated.
- API keys have high entropy, appear once at provisioning, are stored only as SHA-256 hashes, and are selected by a non-secret prefix. Credentials are source-bound, scoped, expirable, revocable, and rotatable while the previous key remains active until revoked. Keys must be stored by operators in a secret manager; they are never accepted in payloads or written to logs/rejection JSON.
- Rejected record JSON is stored only in `ingestion_rejections`; rejected values never enter telemetry/event/state fact tables and never feed operational intelligence.
- Migration 006 is additive and nullable-first for new fact metadata. It does not backfill or rewrite existing historical fact rows. Existing `/events` and `/alerts` behavior remains supported. Rollback is feature disablement; no destructive DDL rollback is provided.

## Inspection findings and component disposition

| Area | Observed implementation | Disposition |
|---|---|---|
| Relational model | Buildings → locations → assets, with system and metric/event catalogues; fact tables reference assets. | Retained and extended additively. |
| Ingestion | Legacy seed SQL and APIs plus the versioned source-authenticated batch route. | Core validates canonical telemetry/event/state batches; no vendor SDK logic. |
| Historian | Migration 006 adds received time, source, quality, batch audit and append-only state facts. | Retained; freshness/gaps, retention, and rollups remain future work. |
| Intelligence | Existing KPI, alert/anomaly rules, incident grouping/lifecycle, evidence validation, explanation/recommendation, dashboard and chat code is active. | Retain; require compatibility/parity checks for any schema changes. |
| Hardcoded/simulated dependencies | Synthetic seed; fixed building/system catalogues and operational assumptions; event/metric catalogue knowledge also exists in code. | Remove only dependencies that block vendor-neutral Core operation; preserve demo behavior and calibrated rules. |
| Duplicate/inconsistent structures | Older architecture drafts remain non-authoritative; asset health and recovery relationships are computed in multiple paths. | Runtime migrations/code remain authoritative. Further consolidation is outside this approval. |

Potential regression risks are existing API response compatibility, alert/evidence joins by fact IDs, duplicate behavior, dashboard/chat status parity, and migration-on-existing-volume behavior. No actual vendor connector is present or needed for this Core sprint.

## Documentation inventory

All 32 repository Markdown files were inventoried. The authoritative architecture, canonical model, historian, migration, issue, ingestion-contract, README, runtime-schema, and current verification documents were read against code/schema where relevant. Historical reports are retained as dated evidence and are not rewritten to imply a current test run.

| File | Purpose and current assessment | Week 3 coverage / remaining gap |
|---|---|---|
| `README.md` | Operator overview. Updated with v1 ingestion routes and existing-database migration pointer. | Route and contract navigation is current. |
| `docs/README.md` | Second operator/project guide; overlaps root README. | Useful navigation; duplicate overview should remain cross-linked rather than expanded. |
| `docs/CORE_DATA_ARCHITECTURE.md` | Runtime baseline and architecture target. Updated with pre-006 vs repository schema status. | Ingestion and additive migration are marked implemented; unrelated target changes remain proposed. |
| `docs/CANONICAL_DATA_MODEL.md` | Canonical model and implementation status. | Marks the approved ingestion defaults and migrations 006–008 implemented. |
| `docs/HISTORIAN_DESIGN.md` | Historian design plus implementation status. | Marks provenance/quality/state ingestion implemented; freshness/gap tracking and retention remain future work. |
| `docs/DATA_MIGRATION_PLAN.md` | Migration and deployment plan. | Documents additive 006, runner command, no historical backfill, and compatible rollback. |
| `docs/SPRINT_1_TECHNICAL_ISSUES.md` | Backlog with current statuses for ingestion/migration/building scope. | Explicitly separates implemented source scope from unapproved tenant/organization work. |
| `docs/INGESTION_CONTRACT.md` | v1 connector wire contract. | Documents request/response, commit semantics, idempotency, key security/lifecycle and conflict behavior. |
| `docs/CONTRACT_TO_DB_MAPPING.md` | Mapping from v1 contract to PostgreSQL. | Reconciled with actual migration 006–008 structures and constraints. |
| `docs/CONNECTOR_ARCHITECTURE.md` | Proposed boundary between Core and connector projects. | Covers vendor independence; no connector implementation is in scope. |
| `docs/API_CONTRACT.md` | Broader API proposal. | Includes routes/behavior beyond runtime; treat unimplemented routes as proposals. |
| `docs/API_CONTRACT_V1.md` | Older API contract draft. | Its connector ingestion section is labeled superseded by `INGESTION_CONTRACT.md`. |
| `docs/ARCHITECTURE.md` | Earlier architecture proposal. | Contains older model/runtime assumptions; not authoritative for implementation. |
| `docs/DATA_MODEL.md` | Earlier data-model draft. | Clearly marked non-authoritative and points readers to migration 006/current canonical model. |
| `docs/data-model.sql` | Earlier proposed SQL model. | Not mounted/applied; not runtime authority. |
| `docs/DECISION_MATRIX.md` | Decision/recommendation record from architecture work. | Useful context; its decisions must be checked against later approvals and actual code. |
| `docs/INTEGRATION_PLAN.md` | Integration planning. | Future-facing; no vendor integration is implemented in this sprint. |
| `docs/MODULAR_ARCHITECTURE.md` | Modularization proposal. | Supports Core/connector separation; proposed target only. |
| `docs/REPOSITORY_RESTRUCTURE_PLAN.md` | Earlier restructuring plan. | References a different branch/layout; retain as historical, not current repository map. |
| `docs/SPRINT_1_IMPLEMENTATION_PLAN.md` | Sprint work plan. | Planning record; should be reconciled after design decisions and implementation. |
| `docs/DEPLOYMENT_STANDARD.md` | Local deployment instructions and 19 November clean-clone evidence pointer. | Local clean install and readiness steps verified; production security/upgrade rollout remains unverified. |
| `docs/RUN_DEMO.md` | Demo operating instructions. | Applies to synthetic demo; not proof of live connector behavior. |
| `docs/DATABASE_BASELINE.md` | Historical pre-006 schema baseline plus migration 006–008 pointers. | Distinguishes the inspected shared database from the repository target. |
| `docs/RUNTIME_VERIFICATION.md` | Dated runtime verification record. | Historical verification; does not establish this review's API connectivity. |
| `docs/BASELINE_TEST_REPORT.md` | Prior test baseline. | Historical results only; current baseline here was unit suite 25/25. |
| `docs/DAY2_AHMED_HANDOVER.md` | Day 2 technical handover. | Historical implementation details and test commands; some old paths need future cleanup. |
| `docs/DAY2_CORRECTION_REPORT.md` | Day 2 corrections/status. | Dated record; not a current productization report. |
| `docs/DAY2_IMPLEMENTATION_REPORT.md` | Day 2 feature implementation record. | Historical; preserve original results. |
| `docs/DAY2_TEST_RESULTS.md` | Authoritative Day 2 verification results. | Valid for its recorded revision/environment, not a current rerun. |
| `docs/DAY2_VERIFICATION_AND_TEST_RESULTS.md` | Older verification record. | Explicitly historical/superseded by Day 2 results. |
| `docs/DAY3_DATA_VALIDATION.md` | Bandwidth and Day 3 validation. | Results are tied to commit `1ae9eef`; later commit `83ec468` is the current checkout. Preserve as historical evidence. |
| `docs/DAY3_SUBMISSION_STATUS.md` | Day 3 submission checklist. | Historical/superseded; does not verify current UI or ingestion. |
| `docs/CONTRACT_CHANGES.md` | Day 2 contract amendments. | Historical API changes; validate against current contract before further API work. |

Historical test text files and binary ERD/presentation assets are preserved; they are not competing runtime schemas. No README was deleted.

## Documents changed and created

Updated existing documents:

- `CORE_DATA_ARCHITECTURE.md` — repository/runtime source-of-truth date, current module layout, telemetry path, uniqueness finding, and migration-runner status.
- `CANONICAL_DATA_MODEL.md` — approved ingestion implementation status and migration numbering.
- `HISTORIAN_DESIGN.md` — implemented ingestion/provenance/state scope and deferred freshness/retention.
- `DATA_MIGRATION_PLAN.md` — documents migrations 006–008, existing-volume rollout, and safe rollback.
- `SPRINT_1_TECHNICAL_ISSUES.md` — refreshed status for the ingestion, migration, and source/building items.

New document:

- `PRODUCTIZATION_IMPLEMENTATION_REPORT.md` — necessary to record the requested current implementation assessment, complete Markdown inventory, verification boundary, and architectural gate. Existing Day 2/Day 3 reports are revision-specific history and do not serve this purpose.

Implementation includes the additive migration, runner, provisioning CLI, versioned batch API, validation/persistence service, audit lookup, and quality-aware telemetry readers. Existing historical facts are not modified. Isolated regression results are recorded below.

## Verification and limits

- Post-change unit suite — **28/28 passed**; existing operational intelligence suite — **43/43 passed**; migration/data integrity suite — **21/21 passed**; E2E scenario — **14/14 passed**.
- The shared database remains at pre-006 schema; all mutable integration suites ran against a separate disposable Compose volume/project.
- Frontend/API integration suite on the Phase 0.1 baseline — **19/20**; resolved in Phase 0.2 below. The valid telemetry view retains every old NULL-quality fact; Day 2 and E2E operational checks pass.
- Read-only `scripts/verify_schema.sql` on the isolated migrated database — **OVERALL OK** (20 tables, 8 views and expected indexes/columns). Python compilation and `git diff --check` pass. Docker build from registry was unavailable, so tests used a cached local API image with the current workspace mounted.
- No migration was applied to the shared database; historical records there remain untouched.

## Deployment handoff

The approved defaults remove the contract decision gate. Release requires isolated post-change regression results and an operator-reviewed deployment of migrations 006–008 to the existing database. No live migration is performed as part of repository implementation.

## Phase 0.1 — clean disposable baseline re-run (19 November 2026)

The baseline was rerun on branch `feature/ingestion-foundation`, HEAD `139be55`, using fresh Compose project `anzma-phase0-20261119`, fresh volume `anzma-phase0-20261119_pgdata`, PostgreSQL 16, and API port 18001. The checkout was clean before this evidence update. The shared `anzma-insight` project and database were not used. The disposable stack started healthy; its fresh volume initialized migrations through 006.

| Suite / command (run in disposable API container) | Result | Evidence |
|---|---:|---|
| `python tests/unit/test_day2_ahmed.py` | **28/28 PASS** | All unit checks passed. |
| `env PGHOST=db PGDATABASE=anzma python tests/integration/test_day2_integration.py` | **43/43 PASS** | Operational intelligence, evidence, incidents and fallback checks passed. |
| `env PGHOST=db PGDATABASE=anzma python tests/integration/test_data_integrity.py` | **21/21 PASS** | Fresh test DB applied migrations repeatedly; additive/history, source mapping, quality view and existing integrity checks passed. |
| `env PGHOST=db PGDATABASE=anzma python tests/integration/test_frontend_api.py` | **19/20** | New v1 ingestion and legacy API checks passed. One existing assertion failed: `tests/integration/test_frontend_api.py:227` expects CRAC not critical after the same suite runs `/alerts/run`, which creates a critical CRAC alert. This is NOT marked green or waived; resolution is Phase 0.2. |
| `python tests/integration/test_chat_consistency.py http://127.0.0.1:8000` | **31/31 PASS** | Read-only chat/dashboard agreement against the clean stack. |
| `env PYTHONPATH=/app PGHOST=db PGDATABASE=anzma python tests/integration/test_dashboard.py` | **PASS** | Read-only summary returned 12 monitored assets and KPI values. |
| `env PGHOST=db PGDATABASE=anzma python tests/scenarios/e2e_s1.py --build` | **14/14 PASS** | S1 alert, incident, evidence, grounding and idempotent second detection passed. |
| `scripts/verify_schema.sql` via the DB adapter on fresh `anzma` | **OVERALL OK** | Expected tables, views, indexes, columns and constraints all passed. |

The failing assertion is consistent with the earlier known baseline and has not been weakened or changed in item 0.1. Test output included the expected S1 CRAC alert as critical, so Phase 0.2 must establish whether order/state leakage or the assertion's post-detection assumption is wrong. The clean run is evidence for item 0.1 only; Phase 0 acceptance is not yet met.

## Phase 0.2 — order-independent dashboard regression (19 November 2026)

Diagnosis: the prior assertion examined the seeded CRAC after the test suite's earlier `POST /alerts/run` case had created an open critical alert. The dashboard correctly reports an asset with an open/acknowledged critical alert as `CRITICAL`; fault recovery events affect recovered event-state calculations but do not silently resolve an alert. The failure was a fixture/assumption collision caused by test ordering, not random application state leakage.

Change: `tests/integration/test_frontend_api.py` now creates a temporary HVAC asset with a critical `hvac_fault` followed by `hvac_recovered`, then asserts that the historical `criticals`/`faults` remain visible while `active_criticals=0` and the status is non-critical. It separately asserts that the seeded CRAC status agrees with whether an open critical alert exists. The temporary asset and event rows are removed in `finally`. No production behavior or assertion intent was weakened.

Verification on the clean disposable Compose stack:

- Full command `env PGHOST=db PGDATABASE=anzma python tests/integration/test_frontend_api.py` — **20/20 passed**.
- An isolated test invocation ran equipment-health before `/alerts/run`, then ran `/alerts/run`, then repeated equipment-health — **both order cases passed**.
- `python3 -m py_compile tests/integration/test_frontend_api.py` — passed.

The test now checks both original intent (a recovered fault remains historical, not active) and current dashboard behavior (a still-open critical alert remains critical) independently of test order.

## Phase 0.3 — event source identity migration (19 November 2026)

Added `db/migrations/007_event_source_identity.sql`. It first creates the unique partial legacy index `(asset_id, event_type_id, ts) WHERE source_event_id IS NULL`, then drops the old full unique constraint. This preserves uniqueness for legacy events while permitting source events with different IDs to share the same asset/type/timestamp. No event rows are rewritten or deleted. The legacy `POST /events` route infers the partial index when present and uses its former conflict target before migration 007, retaining compatibility during rollout. The ingestion service now includes `observed_at` in exact event retry comparison; reuse of an ID with changed detail or time is `CONFLICT`.

Verification in the disposable Compose project:

- `env PGHOST=db PGDATABASE=anzma python tests/integration/test_frontend_api.py` — **20/20 passed**. This includes two source event IDs at one instant accepted, changed content/time for an existing ID rejected `CONFLICT`, and duplicate legacy `POST /events` returning 409.
- `env PGHOST=db PGDATABASE=anzma python tests/integration/test_data_integrity.py` — **22/22 passed**. Migrations 003–008 are applied twice; a direct database test proves source event IDs can coexist and the legacy null-ID key still rejects duplicates.
- `env PGHOST=db PGDATABASE=anzma python tests/integration/test_day2_integration.py` — **43/43 passed**; `env PGHOST=db PGDATABASE=anzma python tests/scenarios/e2e_s1.py --build` — **14/14 passed**; `python tests/unit/test_day2_ahmed.py` — **28/28 passed**.
- `python scripts/apply_migrations.py` on the isolated default DB (at 006) printed `SKIP 006...` / `APPLIED 007...`; a second run printed `SKIP 006...` / `SKIP 007...`.
- Read-only `scripts/verify_schema.sql` after 007 — **OVERALL OK** (20 base tables, 8 views, expected indexes and columns).

The integrity suite initially failed because the new test expected a legacy duplicate rejection before inserting the first legacy row. The fixture was corrected to insert one null-source row, then verify the second is rejected; the suite then passed 22/22. This was a test setup error, not an application or migration failure. The shared database remains at its prior schema and was not touched.

## Phase 0.4 and 0.6 — contract alignment and conformance (19 November 2026)

Added migration `008_ingestion_contract_conformance.sql`; it only adds nullable/defaulted columns, constraints, an index and a view. It does not rewrite or delete existing facts or rejection records. The v1 handler now separates unknown from incompatible metrics/events, reports `ASSET_NOT_PERMITTED`, distinguishes `AUTH_MISSING` and `AUTH_INVALID`, and consistently uses `SCOPE_DENIED`. Records older than 90 days are rejected unless `data_sources.max_backdate_days` overrides the default. `scripts/ingestion_admin.py` can set/reset that override. Batch rows retain `delivery_mode` and optional `connector_status`; rejection audit stores nullable source/external identifiers (legacy rows stay untouched) and powers `v_unmapped_external_assets`. Optional metric bounds mark out-of-range values `BAD` with a warning.

`tests/integration/test_frontend_api.py` now includes database-backed assertions for all 16 §13.3 vectors plus the new 0.4 cases, including 101-rejection response truncation and complete GET audit retrieval. Its single-command run is `python tests/integration/test_frontend_api.py`.

Final verification on the disposable Compose project `anzma-phase0-20261119` (not the shared `anzma-insight` DB): unit **28/28**, data integrity **23/23**, frontend/API plus all conformance vectors **21/21**, Day 2 operational intelligence **43/43**, E2E S1 **14/14**, chat consistency **31/31**. Dashboard smoke command `PYTHONPATH=/app python tests/integration/test_dashboard.py` returned KPIs for 12 assets. Migration runner applied 008 once, then skipped it on rerun; read-only `scripts/verify_schema.sql` returned **OVERALL OK** (20 tables, 8 views). `py_compile` and `git diff --check` passed after the code changes. The first API test attempt exposed old 2000 timestamps in a fixture; these were moved to within the contract's accepted range, not used to weaken any assertion.

At the time this Phase 0.4/0.6 section was written, items 0.5 and 0.7 had not yet been completed. Their subsequent evidence is listed in the Phase 0 completion table above. No Phase 1 work was started. The shared database remains unchanged.
