# DAY2_IMPLEMENTATION_REPORT.md

What was changed, in which commit, and by whom, using the pushed repository structure. Schema decisions live in [DATABASE_BASELINE.md](DATABASE_BASELINE.md); verified results in [DAY2_TEST_RESULTS.md](DAY2_TEST_RESULTS.md); the response to the Day 2 review in [DAY2_CORRECTION_REPORT.md](DAY2_CORRECTION_REPORT.md).

## 1. Pushed structure (old path to current path)

| Old | Current |
|---|---|
| `App.py` | `app/main.py` |
| `app/chat_lite.py`, `chat_facts.py`, `frontend_api.py` | `app/api/` |
| `day2_ahmed/db_adapter.py` | `app/adapters/database/db_adapter.py` |
| `day2_ahmed/ai_explainer.py` | `app/adapters/model_providers/ai_explainer.py` |
| `day2_ahmed/alert_engine.py`, `alert_config.py` | `app/domain/alerts/` |
| `day2_ahmed/incident_engine.py` | `app/domain/incidents/` |
| `day2_ahmed/evidence.py`, `validation.py` | `app/domain/evidence/` |
| `day2_ahmed/recommendations.py` | `app/domain/ai/recommendations.py` |
| `day2_ahmed/integrity_rules.py` | `app/domain/operations/integrity_rules.py` |
| `day2_ahmed/pipeline.py` | `app/services/pipeline.py` |
| `app/dashboard_core_pg.py` | `app/repositories/dashboard_core_pg.py` |
| `day2_ahmed/test_*.py`, `e2e_s1.py` | `tests/unit/`, `tests/integration/`, `tests/scenarios/` |
| `day2_ahmed/migrations/003_incidents.sql` | `db/migrations/003_incidents.sql` (004, 005 alongside) |
| `Dockerfile`, `docker-compose.yml` | `deploy/compose/` |

## 2. Changes and authors

| Change | Files (current paths) | Commit | Author |
|---|---|---|---|
| C1: alert uniqueness: migration 005 and `INSERT ... ON CONFLICT DO NOTHING` in `store_alerts` | `db/migrations/005_alert_uniqueness.sql`, `app/adapters/database/db_adapter.py` | session of `87e084d` (imported in `73baaf7`) | Ahmed |
| C2: data integrity tests (18) | `tests/integration/test_data_integrity.py` | same | Ahmed |
| C3: schema verification and baseline docs | `scripts/verify_schema.sql`, `docs/DATABASE_BASELINE.md` | same | Ahmed |
| C4: test bootstraps apply 003, 004, 005 | `tests/integration/test_day2_integration.py`, `test_frontend_api.py`, `tests/scenarios/e2e_s1.py` | same | Ahmed |
| Chat views (004), Compose init mounts, `OLLAMA_HOST` default | `db/migrations/004_chat_views.sql`, `deploy/compose/docker-compose.yml`, `app/api/chat_*.py` | same | Amr |
| Repository restructure and Compose/Dockerfile moves | whole tree | `73baaf7`, `72d9bcf` | Amr |
| Fix `ai_explainer` import so the API starts; pip timeout | `app/domain/ai/recommendations.py`, `app/domain/incidents/incident_engine.py`, `deploy/compose/Dockerfile` | `b26b324` | Ahmed |
| Test path fixes after the restructure | `tests/integration/*.py` | `ff028d0`, `740eae7` | Ahmed |
| Fix stale imports hidden by a broad `except` (regression, 16/17 to 17/17) | `app/api/chat_lite.py` | `740eae7` | Ahmed |

## 3. Migration history

base (`db/init/anzma_schema_and_seed.sql`) to 003 (incidents) to 004 (chat views) to 005 (alert uniqueness). Each is idempotent; there is no migration runner. Verified on the running database: see DATABASE_BASELINE.md section 7.

## 4. Tests

Latest automated verification (tested application commit `ab88f5f`): 25 + 43 + 18 + 18 + 31 = **135/135 PASS**; `verify_schema.sql` OVERALL OK; readiness PASS; S1 scenario 14/14; dashboard summary PASS. Deterministic fallback passed. Live Qwen evidence is historical from `740eae7` and was not rerun on `ab88f5f`. Details, commands and the items not executed: [DAY2_TEST_RESULTS.md](DAY2_TEST_RESULTS.md).

## 5. Not done / limitations

Live Qwen verification on `ab88f5f`, the Compose-container Ollama path, and the demo recording remain open (see the correction report). `e2e_s1.py`, `test_dashboard.py`, and readiness are verified on `ab88f5f`. Bandwidth unit (C5), data-clock strategy, migration runner and the proposed new entities remain out of scope.