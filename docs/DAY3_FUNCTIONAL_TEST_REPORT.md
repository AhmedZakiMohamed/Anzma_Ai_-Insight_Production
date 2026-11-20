# Phase 0 local functional verification

**Run date:** 19 November 2026 (UTC)  
**Branch:** `feature/ingestion-foundation`  
**Base HEAD:** `8cf0bf22a7652254cae95a506a7b067e25a73e8f`
**Tree tested:** uncommitted working-tree changes based on that HEAD. No commit was created because the task explicitly prohibited commits.
**Isolation:** test databases and the restored H0 database were inside the new `anzma-phase0-closure` PostgreSQL 16 project. No shared/live database or existing Docker volume was used.

## Regression and configuration suites

| Suite | Exact command | Result | Date / tested tree |
|---|---|---:|---|
| Day 2 unit | `docker exec anzma-phase0-closure-api-1 python tests/unit/test_day2_ahmed.py` | **28/28 PASS** | 2026-11-19, base HEAD `8cf0bf2` + worktree |
| Data integrity/migrations | `docker exec anzma-phase0-closure-api-1 python tests/integration/test_data_integrity.py` | **23/23 PASS** | 2026-11-19, base HEAD `8cf0bf2` + worktree |
| Day 2 operational intelligence | `docker exec anzma-phase0-closure-api-1 python tests/integration/test_day2_integration.py` | **43/43 PASS** | 2026-11-19, base HEAD `8cf0bf2` + worktree |
| Chat/dashboard consistency | `docker exec anzma-phase0-closure-api-1 python tests/integration/test_chat_consistency.py http://127.0.0.1:8000` | **31/31 PASS** | 2026-11-19, bearer auth required |
| Frontend/API, auth, legacy routes and ingestion conformance | `docker exec anzma-phase0-closure-api-1 python tests/integration/test_frontend_api.py` | **38/38 PASS** | 2026-11-19, final rebuilt worktree |
| Snapshot comparator behavior | `python3 tests/unit/test_snapshot_compare.py` | **3/3 PASS** | 2026-11-19 |
| Deployment configuration | `python3 tests/unit/test_deploy_security.py` | **4/4 PASS** | 2026-11-19 |
| Dashboard smoke | `docker exec -e PYTHONPATH=/app anzma-phase0-closure-api-1 python tests/integration/test_dashboard.py` | **PASS; 12 monitored assets** | 2026-11-19; bounded latest 31-day window |
| S1 fallback E2E | `docker exec -e OLLAMA_HOST=http://127.0.0.1:1 -e OLLAMA_TIMEOUT=1 anzma-phase0-closure-api-1 python tests/scenarios/e2e_s1.py --build --llm` | **15/15 PASS** | 2026-11-19; deliberately unreachable local model endpoint |

The seven counted suites total **170/170** (28 + 23 + 43 + 31 + 38 + 3 + 4). The dashboard smoke script reports its result without a numbered count. The separate E2E outage/fallback run passed 15/15 and reported that the model was unavailable, the deterministic explanation used stored evidence, evidence checks passed, and recommendations came from approved playbooks. This does **not** establish that live model inference works.

## Authentication and deployment checks

The Compose project was resolved and started with a new project name, volume, database password, and high-entropy API token. Secret values were not printed. `docker compose config` showed these host publications:

```text
db ports= [('127.0.0.1', '15438', 5432)]
ollama ports= [('127.0.0.1', '11436', 11434)]
api ports= [(None, '18007', 8000)]
api bearer token configured= True
```

HTTP checks returned `/health` 200 and `/ready` 200. Protected REST returned `401 AUTH_MISSING` without credentials, `401 AUTH_INVALID` for an invalid token, and 200 with the configured token. `POST /auth/session` returned 200 with an HttpOnly, SameSite=Strict cookie; cookie-authenticated `/incidents` returned 200; logout cleared it and access returned 401. Static assets, `/dashboard`, `/docs`, and `/openapi.json` are intentionally public. Normal REST and Socket.IO use the same deployment token; v1 ingestion continues to use its separate source-scoped key.

Real network Socket.IO checks rejected missing and invalid credentials and accepted both a valid session cookie and a valid bearer token. The human browser login rendering and interaction are **NOT TESTED**.

An isolated first ingestion request created source `p0-closure-e3a1a68f`, mapped `first-device`, and submitted one 22.5 C temperature record. It returned HTTP 200, `ACCEPTED`, `COMMITTED`, with one accepted record. The key stayed in process memory and was withheld from output. The frontend/API suite also confirmed legacy `POST /events` and `POST /alerts` still work.

## §13.3 vector-to-test mapping

Every vector has an independent test name and assertion in `tests/integration/test_frontend_api.py`; the final suite passed 38/38:

| Vector | Test name |
|---:|---|
| 1 | `Vector 1: valid envelope accepts three records` |
| 2 | `Vector 2: exact batch replay returns stored result` |
| 3 | `Vector 3: changed payload reusing batch_id returns 409 BATCH_ID_REUSED` |
| 4 | `Vector 4: same telemetry key and value in a new batch is a duplicate` |
| 5 | `Vector 5: conflicting telemetry preserves the first value` |
| 6 | `Vector 6: timestamp with a non-UTC offset is INVALID_TIMESTAMP` |
| 7 | `Vector 7: timestamp ten minutes in the future is FUTURE_TIMESTAMP` |
| 8 | `Vector 8: door_forced on a camera is EVENT_NOT_COMPATIBLE` |
| 9 | `Vector 9: Fahrenheit telemetry is rejected with UNIT_MISMATCH` |
| 10 | `Vector 10: unmapped external ID is rejected and audited` |
| 11 | `Vector 11: quality MISSING is SCHEMA_INVALID` |
| 12 | `Vector 12: state with only communication_status is SCHEMA_INVALID` |
| 13 | `Vector 13: 1000 records with ten invalid records return PARTIAL` |
| 14 | `Vector 14: 5001 records return 413 BATCH_TOO_LARGE` |
| 15 | `Vector 15: wrong source_code for key returns 403 SOURCE_MISMATCH` |
| 16 | `Vector 16: empty valid batch returns 200 and updates source liveness` |

The same frontend/API run covers exact event retry/conflict identity, distinct source event IDs at one asset/type/time, legacy duplicate `POST /events`, auth error separation, 90-day back-date policy, source scopes, rejection audit/truncation, and plausibility quality behavior. The CRAC recovery assertion preserves its original purpose through a dedicated fault/recovery fixture; the seeded CRAC separately follows active alert state. All passed in the 38-case suite.

The 2001 compatibility event is explicitly created by that suite in `TEST_DB = "anzma_api_test"`; the script creates this database before loading schema and fixtures. In the isolated closure cluster, a direct read returned `anzma_api_test 2001_test_event_count=1`, `anzma 2001_test_event_count=0`, and `h0_rehearsal 2001_test_event_count=0`. The fixed dashboard smoke bounds itself to the latest 31-day interval and reported 12 assets. The original/shared database was not queried or modified.

## H0 snapshot and database result

The current-code rerun restored the supplied H0 dump, applied 006–008, verified the ledger no-op on rerun, matched every original table count, retained legacy NULL provenance/quality, and returned `OVERALL OK` from `scripts/verify_schema.sql`. Full commands, raw outputs, `\d event_records`, SQL average evidence, and hashes are in [`DAY3_MIGRATION_REHEARSAL.md`](DAY3_MIGRATION_REHEARSAL.md).

The provided H0 snapshots and final post-migration API response files had matching SHA-256 values for dashboard, alerts, and incidents. `scripts/compare_api_snapshots.py` returned `RESULT differences=0 tolerance_only=0` for all three. No fields, timestamps, or time-dependent values were excluded. The earlier reported `avg_zone_temp` alternate representation differed by `3.5527136788005009e-15 C`; PostgreSQL `AVG(DOUBLE PRECISION)` over 291 samples in the captured H0 window returned the exact snapshot value. The comparator retains `rel_tol=1e-12` and `abs_tol=1e-12`; tests prove that a 0.01 C difference fails and nonnumeric fields remain exact.

## NOT TESTED / deferred

- Successful live Ollama inference: the isolated Qwen pull reached 99% then failed with `dial tcp 104.18.17.170:443: i/o timeout`; follow-up `ollama list` was empty and no live response was claimed. Rerun `docker compose --env-file /private/tmp/anzma-phase0-closure/.env -p anzma-phase0-closure -f deploy/compose/docker-compose.yml run --rm -T ollama-init`, then `docker exec -e OLLAMA_HOST=http://ollama:11434 -e OLLAMA_TIMEOUT=120 anzma-phase0-closure-api-1 python tests/scenarios/e2e_s1.py --build --llm`.
- Clean Git clone of the final closure tree: not possible without a commit, which was prohibited. The current worktree itself was built into a new isolated Compose project; the prior clean clone at `07bf77e` predates these changes.
- Human browser visual acceptance.
- Shared/live database migration and production backup/restore.
- Real vendor/simulator connector, freshness/gap job, and retention policy: deferred to Phase 1.
