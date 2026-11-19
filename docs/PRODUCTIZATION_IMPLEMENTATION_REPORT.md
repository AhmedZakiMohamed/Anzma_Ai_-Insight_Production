# Productization implementation report

**Review date:** 18 November 2026  
**Checkout:** `feature/ingestion-foundation` (uncommitted implementation changes)
**Scope:** Core only. No vendor connector or Milestone work.

## Executive report

The current application is a single-process FastAPI/Socket.IO service backed by PostgreSQL 16. Its active operational path is `app/main.py` → `app/services/pipeline.py` → domain alert/incident/evidence/AI modules → `app/adapters/database/db_adapter.py`; dashboard queries are in `app/repositories/dashboard_core_pg.py` and `app/api/frontend_api.py`. Chat routes and compatibility views are active. The old modules under `legacy/` are not the canonical runtime path.

The previously deployed schema is the base schema followed by migrations 003–005. Additive migration 006 now defines a migration ledger, source credentials, source-scoped asset mappings, provenance columns, batch/rejection audit, append-only state facts, and quality-aware compatibility views. The application migration runner upgrades an existing database only when explicitly invoked; Compose init mounts include 006 only for a fresh database volume. The prior read-only query confirmed the existing runtime had 13 base tables; migration 006 has not been applied to the shared database.

Existing stored telemetry and events already form a basic historical record. Multiple buildings, locations, systems, and assets are represented. The current ingestion flow is seeded SQL plus the single-record `POST /events` and manual alert `POST /alerts` routes; telemetry cannot be submitted through a Core API. Operational processing continues from stored facts into alert and anomaly detection, severity classification, incident correlation/lifecycle, evidence re-verification, grounding-only AI explanations, playbook recommendations, dashboard queries, and chat/tool routing.

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
| `docs/CANONICAL_DATA_MODEL.md` | Canonical model and implementation status. | Marks the approved ingestion defaults and migration 006 implemented. |
| `docs/HISTORIAN_DESIGN.md` | Historian design plus implementation status. | Marks provenance/quality/state ingestion implemented; freshness/gap tracking and retention remain future work. |
| `docs/DATA_MIGRATION_PLAN.md` | Migration and deployment plan. | Documents additive 006, runner command, no historical backfill, and compatible rollback. |
| `docs/SPRINT_1_TECHNICAL_ISSUES.md` | Backlog with current statuses for ingestion/migration/building scope. | Explicitly separates implemented source scope from unapproved tenant/organization work. |
| `docs/INGESTION_CONTRACT.md` | v1 connector wire contract. | Documents request/response, commit semantics, idempotency, key security/lifecycle and conflict behavior. |
| `docs/CONTRACT_TO_DB_MAPPING.md` | Mapping from v1 contract to PostgreSQL. | Reconciled with actual migration 006 structures and constraints. |
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
| `docs/DEPLOYMENT_STANDARD.md` | Deployment conventions. | Setup guidance; does not implement migration-on-existing-volume. |
| `docs/RUN_DEMO.md` | Demo operating instructions. | Applies to synthetic demo; not proof of live connector behavior. |
| `docs/DATABASE_BASELINE.md` | Historical pre-006 schema baseline plus migration 006 pointer. | Distinguishes the inspected shared database from the repository target. |
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
- `DATA_MIGRATION_PLAN.md` — documents migration 006, existing-volume rollout, and safe rollback.
- `SPRINT_1_TECHNICAL_ISSUES.md` — refreshed status for the ingestion, migration, and source/building items.

New document:

- `PRODUCTIZATION_IMPLEMENTATION_REPORT.md` — necessary to record the requested current implementation assessment, complete Markdown inventory, verification boundary, and architectural gate. Existing Day 2/Day 3 reports are revision-specific history and do not serve this purpose.

Implementation includes the additive migration, runner, provisioning CLI, versioned batch API, validation/persistence service, audit lookup, and quality-aware telemetry readers. Existing historical facts are not modified. Isolated regression results are recorded below.

## Verification and limits

- Post-change unit suite — **28/28 passed**; existing operational intelligence suite — **43/43 passed**; migration/data integrity suite — **21/21 passed**; E2E scenario — **14/14 passed**.
- The shared database remains at pre-006 schema; all mutable integration suites ran against a separate disposable Compose volume/project.
- Frontend/API integration suite — **19/20 passed**. The new v1 ingestion and legacy API compatibility tests passed. One existing seeded dashboard assertion expects CRAC to be non-critical after the same suite calls detection and opens a critical CRAC alert. The valid telemetry view retains every old NULL-quality fact; Day 2 and E2E operational checks pass.
- Read-only `scripts/verify_schema.sql` on the isolated migrated database — **OVERALL OK** (20 tables, 7 views and expected indexes/columns). Python compilation and `git diff --check` pass. Docker build from registry was unavailable, so tests used a cached local API image with the current workspace mounted.
- No migration was applied to the shared database; historical records there remain untouched.

## Deployment handoff

The approved defaults remove the contract decision gate. Release requires isolated post-change regression results and an operator-reviewed deployment of migration 006 to the existing database. No live migration is performed as part of repository implementation.

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
