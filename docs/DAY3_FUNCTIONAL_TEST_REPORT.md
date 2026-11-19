# Day 3 functional test report

**Run date:** 19 November 2026 (UTC)  
**Branch:** `feature/ingestion-foundation`  
**Source commit tested:** `a9de778cf0793a530ae6e347480f07820c17ab7d` (source code did not change in the later docs-only commit)  
**Database boundary:** all mutable tests used disposable PostgreSQL databases inside `anzma-h0-rehearsal` or fresh `anzma-item07-*` Compose projects. No shared/live database was used.

## Suite results

| Suite / command | Count/result | Date | Commit under test |
|---|---:|---|---|
| `docker exec -e PGHOST=db -e PGPORT=5432 -e PGUSER=postgres -e PGPASSWORD=disposable-h0-only anzma-h0-rehearsal-api python tests/unit/test_day2_ahmed.py` | **28/28 PASS** | 2026-11-19 | `a9de778` |
| `docker exec -e PGHOST=db -e PGPORT=5432 -e PGUSER=postgres -e PGPASSWORD=disposable-h0-only anzma-h0-rehearsal-api python tests/integration/test_data_integrity.py` | **23/23 PASS** | 2026-11-19 | `a9de778` |
| `docker exec -e PGHOST=db -e PGPORT=5432 -e PGUSER=postgres -e PGPASSWORD=disposable-h0-only anzma-h0-rehearsal-api python tests/integration/test_day2_integration.py` | **43/43 PASS** | 2026-11-19 | `a9de778` |
| `docker exec -e PGHOST=db -e PGPORT=5432 -e PGUSER=postgres -e PGPASSWORD=disposable-h0-only anzma-h0-rehearsal-api python tests/integration/test_chat_consistency.py` | **31/31 PASS** | 2026-11-19 | `a9de778` |
| `docker exec -e PGHOST=db -e PGPORT=5432 -e PGUSER=postgres -e PGPASSWORD=disposable-h0-only anzma-h0-rehearsal-api python tests/integration/test_frontend_api.py` | **37/37 PASS** | 2026-11-19 | `a9de778` |
| `docker exec -e PYTHONPATH=/app -e PGHOST=db -e PGPORT=5432 -e PGDATABASE=anzma_h0_rehearsal -e PGUSER=postgres -e PGPASSWORD=disposable-h0-only anzma-h0-rehearsal-api python tests/integration/test_dashboard.py` | **PASS; 12 monitored assets** | 2026-11-19 | `a9de778` |
| `docker exec -e OLLAMA_HOST=http://127.0.0.1:1 -e OLLAMA_TIMEOUT=1 -e PGHOST=db -e PGPORT=5432 -e PGUSER=postgres -e PGPASSWORD=disposable-h0-only anzma-h0-rehearsal-api python tests/scenarios/e2e_s1.py --build --llm` | **15/15 PASS; deterministic fallback used** | 2026-11-19 | `a9de778` |

The five counted test suites total **162/162**. The dashboard smoke script prints its summary without a test count; its output showed `monitored_assets: 12`. The S1 fallback run reported the language model unavailable, a deterministic explanation from stored evidence, and `15/15 checks passed`. A successful live Ollama model response was **NOT TESTED** in this run.

## Frontend/API repeatability on fresh stacks

The frontend/API suite was run once on each newly created Compose project/volume. Each test invocation builds its own disposable `anzma_api_test` database from the repository schema/migrations. The H0 compare project also began with a new volume and was restored only for snapshot comparison before the test database was created.

| Run | Stack and fresh volume | Command | Result | Date | Code commit |
|---|---|---|---:|---|---|
| 1 | `anzma-item07-compare`, `anzma-item07-compare_h0_pgdata` | `docker exec -e PGHOST=db -e PGPORT=5432 -e PGUSER=postgres -e PGPASSWORD=disposable-h0-only anzma-item07-compare-api-1 python tests/integration/test_frontend_api.py` | **37/37 PASS** | 2026-11-19 | `abf44d1` |
| 2 | `anzma-item07-run2`, `anzma-item07-run2_h0_pgdata` | `docker exec -e PGHOST=db -e PGPORT=5432 -e PGUSER=postgres -e PGPASSWORD=disposable-h0-only anzma-item07-run2-api-1 python tests/integration/test_frontend_api.py` | **37/37 PASS** | 2026-11-19 | `abf44d1` |
| 3 | `anzma-item07-run3`, `anzma-item07-run3_h0_pgdata` | `docker exec -e PGHOST=db -e PGPORT=5432 -e PGUSER=postgres -e PGPASSWORD=disposable-h0-only anzma-item07-run3-api-1 python tests/integration/test_frontend_api.py` | **37/37 PASS** | 2026-11-19 | `abf44d1` |

All three runs printed individual PASS lines for vectors 1–16, the existing ingestion/legacy endpoint checks, operational dashboard/API tests, and the separate Step 0.4 behavior check. The process intentionally printed one readiness connection-refused warning while verifying `/ready` against an invalid port; the corresponding readiness test passed.

## §13.3 vector-to-test mapping

Each vector has its own `@test` name and assertion body in `tests/integration/test_frontend_api.py`:

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

The vectors use a shared source/batch fixture established by vector 1, but each vector has its own separately named test and independently asserted outcome.

## Snapshot reproduction

Raw response hashes and the exact recursive comparison output are recorded in [`DAY3_MIGRATION_REHEARSAL.md`](DAY3_MIGRATION_REHEARSAL.md). The reproduction found one difference in `dashboard_overview.json`: `$.kpis[5].value` was `17.872061855670097` in the H0 snapshot and `17.8720618556701` from the migrated API. It is a last-digit floating-point precision delta (~`3.6e-15`). The alerts and incidents responses were byte-identical. No fields were ignored.

## Not tested

- Applying migrations 006–008 to a live/shared database.
- Successful LLM inference from the downloaded Ollama model. The outage/fallback path passed; the live-model path was not exercised here.
- Real vendor/device ingestion through an actual connector; the first clean-install batch was submitted manually through the v1 API.
- Visual browser rendering and interaction in a human browser.
- Running the clean-clone installation on a second physical machine. This was a fresh local clone, project, and volume on the current Docker host.
