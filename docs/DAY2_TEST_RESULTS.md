# Day 2 Test Results (authoritative report)

This is the single authoritative Day 2 test report. Anything not listed as **PASS** here is not claimed as verified. Earlier reports are historical (see section 8).

## 1. What was tested

| Item | Value |
|---|---|
| Tested commit | `740eae72bafaf112f373cbdd8802a66b04de5004` (`740eae7`), `main` = `origin/main` (also in `test_logs/COMMIT.txt`) |
| Code vs tested commit | No differences in `app/`, `tests/`, `deploy/`, `db/`, `scripts/`, `requirements.txt` (`git diff --stat 740eae7 -- app tests deploy db scripts requirements.txt` is empty). Only `docs/` and `test_logs/` differ (documentation and evidence). |
| Date | 2026-11-17 (Africa/Cairo) |
| Machine | MacBook Pro (Apple Silicon, aarch64), Docker Desktop, PostgreSQL 16.16 container, Python 3.11 in the API container |
| Installation | Fresh Compose project `anzma-clean` with a new empty database volume, built from the commit above. The development stack (project `anzma-insight`) was stopped, not deleted, and restarted afterwards; its data was not touched. |
| Model path | Ollama runs natively on the Mac (`qwen2.5:7b`, 7.6B, Q4_K_M). The API container reaches it through the local, git-ignored override `deploy/compose/docker-compose.override.yml` (`OLLAMA_HOST=http://host.docker.internal:11434`); `.env` sets `OLLAMA_MODEL=qwen2.5:7b`. The Compose Ollama container (`ollama-init`) was not used. |

## 2. Summary

| Check | Status | Evidence |
|---|---|---|
| Unit tests | **PASS 25/25** | `test_logs/3_unit.txt` |
| Integration tests | **PASS 43/43** | `test_logs/3_integration.txt` |
| Data integrity tests | **PASS 18/18** | `test_logs/3_integrity.txt` |
| Frontend API tests | **PASS 17/17** (was 16/17, see section 4) | `test_logs/3_frontend.txt` |
| Chat vs dashboard consistency | **PASS 31/31** | `test_logs/3_chat.txt` |
| **Total** | **134/134 PASS, 0 FAIL** | |
| Schema verification (`scripts/verify_schema.sql`) | **PASS** (`OVERALL OK`, 13 tables, 5 views, 005 index present) | `test_logs/1_verify_schema.txt` |
| Live Qwen explanation of a stored alert | **PASS** (alerts 7 and 8) | section 5 |
| Deterministic fallback, shown separately | **PASS** | section 5 |
| Live Qwen assistant chat | **PASS** (`source: OLLAMA`) | `test_logs/4_chat_live.json` |
| Clean install on an empty volume (Mac, host Ollama) | **PASS** | `test_logs/2_ps.txt`, `2_health.txt`, `1_verify_schema.txt` |
| Compose Ollama container path (`ollama-init` model pull) | **NOT EXECUTED successfully**: the pull stayed at "pulling manifest" on this network | n/a |
| `tests/scenarios/e2e_s1.py` | **NOT EXECUTED** on this commit | n/a |
| `tests/integration/test_dashboard.py` | **NOT EXECUTED** on this commit | n/a |
| Readiness including database dependency | **PARTIAL** (section 6) | `test_logs/2_endpoints.txt` |

## 3. Commands

Run from the repository root, inside the API container (`dcc` = `docker compose --env-file .env -p anzma-clean -f deploy/compose/docker-compose.yml -f deploy/compose/docker-compose.override.yml`):

```bash
dcc exec -T api python tests/unit/test_day2_ahmed.py
dcc exec -T api python tests/integration/test_day2_integration.py
dcc exec -T api python tests/integration/test_data_integrity.py
dcc exec -T api python tests/integration/test_frontend_api.py
dcc exec -T api python tests/integration/test_chat_consistency.py
dcc exec -T db psql -U postgres -d anzma -f - < scripts/verify_schema.sql
```

Observed data on the fresh install: 10 alerts and 8 open incidents (4 critical) after detection. The `as_of` timestamp in the detection output moves between runs; the counts do not.

## 4. Regression found and fixed

- On the first run after the restructure the frontend API suite showed **16/17**; the failing test was the chat widget test (HVAC question answered from evidence).
- Cause: `app/api/chat_lite.py` still imported `recommendations`, `pipeline`, `db_adapter`, `evidence`, `validation` and `alert_config` from the removed `day2_ahmed/` folder. A broad `except` hid the failure and set `alert_cfg = None`, so the assistant answered that the configured HVAC rule was unavailable (`grounding.passed: false`).
- Fix (commit `740eae7`): imports corrected to the `app.*` paths, and a failed import now prints its traceback instead of being silent.
- Three test files also had stale paths after the move into `tests/` (`test_day2_integration.py`, `test_data_integrity.py`, `test_frontend_api.py`: repository root, `app.App` to `app.main`, `app.domain.operation` to `app.domain.operations`, `alert_config` import). These were fixed in `ff028d0` and `740eae7`.
- Result after the fix: 17/17. The root cause was found by reading the code and confirmed by the passing rerun.

## 5. Live AI and fallback (stored alerts)

Alert 7 is `HVAC_HIGH_TEMP`; alert 8 is `HVAC_TEMP_ANOMALY` (CRAC Unit 1).

| Field | Live (`use_llm=true`) | Fallback (`use_llm=false`) |
|---|---|---|
| `ai_source` | `llm` | `fallback` |
| `model_used` | `qwen2.5:7b` | `null` |
| Evidence | all rows `verified: true` | all rows `verified: true` |
| Validation | `grounding_supported`, scope `grounding` | `grounding_supported`, scope `grounding` |
| Recommended action | `HVAC_VERIFY` (status `approved`) | `HVAC_VERIFY` (status `approved`) |

Files: `test_logs/5_explain_live_alert7.json`, `5_explain_fallback_alert7.json`, `5_explain_live.json`, `5_explain_fallback.json` (alert 8). `test_logs/5_ollama_ps.json` shows `qwen2.5:7b` loaded in Ollama right after a live call. Alert 8 files were produced on the fresh install; alert 7 files on the restored development stack, same commit and same seed data.

Assistant chat with `use_llm=true` returned `source: OLLAMA` (`test_logs/4_chat_live.json`).

Limits of this evidence:
- `validation.scope = grounding` means values, times and citations exist in the stored evidence and causes/actions come from the approved playbook. It is not a semantic correctness check of the model's wording.
- Observed, not investigated: the live-model chat answer describes the smoke alarm as a current issue, while the dashboard Fire alarm tile reads Operational. The alert is still OPEN because incidents are closed by an operator, so "open" and "currently active" may be conflated. Owner: Amr.

## 6. Readiness and database dependency

- `GET /health` returns `{"status":"ok"}` (liveness only). It does not report database status.
- Compose starts the API only after the database healthcheck passes (`depends_on: db: condition: service_healthy`), and `docker compose ps` showed both services healthy (`test_logs/2_ps.txt`).
- With the database unreachable, every page endpoint answers 503 with a message (frontend API test "database down", PASS).
- **Not implemented:** a readiness endpoint that reports database status. Status: PARTIAL.

## 7. Not executed, partial or open

- Compose-container Ollama path (`ollama-init`): not executed successfully (network).
- `e2e_s1.py` and `test_dashboard.py`: not executed on this commit.
- Readiness endpoint with database status: not implemented.
- Default `OLLAMA_MODEL` in code and Compose is `qwen2.5:3b`; the restructure changed it from `qwen2.5:7b-instruct`. This run used `qwen2.5:7b` from `.env`. Decision to be recorded by the team.
- Playbook approver identity: confirm it is no longer the repository placeholder (owner: Amr).

## 8. Historical results (superseded, not current)

| File | Revision | Note |
|---|---|---|
| `TEST_RESULTS_DAY2.txt`, `TEST_RESULTS_FRONTEND_API.txt`, `TEST_RESULTS_CHAT_CONSISTENCY.txt`, `E2E_S1_OUTPUT.txt` | 2026-11-12, pre-restructure | Amr's and Ahmed's earlier runs |
| `BASELINE_TEST_REPORT.md`, `DAY2_VERIFICATION_AND_TEST_RESULTS.md`, `DAY3_SUBMISSION_STATUS.md` | `87e084d` / `73baaf7`, 2026-11-12 to 2026-11-16 | superseded by this report |
| Earlier "103/103" (25+43+18+17) | `87e084d` plus a dirty working tree | superseded by 134/134 above |

## 9. Reproduce

Follow `docs/RUN_DEMO.md` for the stack, then run the commands in section 3 on the same commit. Record `git rev-parse HEAD` and `git status --porcelain` with the logs.