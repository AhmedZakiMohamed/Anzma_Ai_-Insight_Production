# Day 2 Test Results — Authoritative Report

This is the authoritative Day 2 test report. It records one tested application revision and distinguishes current verification from historical evidence. A check not marked **PASS** is not claimed as verified.

## 1. Tested source and environment

| Item | Result |
|---|---|
| Tested application commit | `ab88f5f9c9b433b1c2caafa22ec31f02bfda6fda` (`ab88f5f`) |
| Branch and worktree at test time | `main`; clean working tree |
| Date | 2026-11-17 (Africa/Cairo) |
| Environment | MacBook Pro, Apple Silicon, Docker Desktop; PostgreSQL 16 and Python 3.11 in the API container |
| Installation | Fresh isolated Compose project `anzma-ab88-verify`, with a newly created PostgreSQL volume. The existing development project and its database were not used. |
| Services in test stack | PostgreSQL and API. A local, untracked Compose override removed host port bindings. Ollama was not started. |
| Durable test record | [`test_logs/ab88f5f_verification_summary.txt`](../test_logs/ab88f5f_verification_summary.txt) |

The later commit `5c3ffb9` synchronized `docs/README.md` with the root README only. No application, test, deployment, database, script, or dependency files changed between `ab88f5f` and `5c3ffb9`. The test results below are therefore tied to the single tested application commit `ab88f5f`; the later documentation commit is not a second application version.

## 2. Results on `ab88f5f`

| Check | Result |
|---|---|
| Unit suite, `tests/unit/test_day2_ahmed.py` | **PASS — 25/25** |
| Day 2 integration suite, `tests/integration/test_day2_integration.py` | **PASS — 43/43** |
| Data integrity suite, `tests/integration/test_data_integrity.py` | **PASS — 18/18** |
| Frontend API suite, `tests/integration/test_frontend_api.py` | **PASS — 18/18** |
| Chat/dashboard consistency suite, `tests/integration/test_chat_consistency.py` | **PASS — 31/31** |
| **Five-suite total** | **PASS — 135/135, 0 failures** |
| Schema verification, `scripts/verify_schema.sql` | **PASS — OVERALL OK**; 13 tables, 5 views, unique alert index from migration 005 present |
| Dashboard summary check, `tests/integration/test_dashboard.py` | **PASS**; read-only summary; invoked with `PYTHONPATH=/app` |
| S1 scenario, `tests/scenarios/e2e_s1.py --build` | **PASS — 14/14**; all 14 evidence rows verified; deterministic fallback produced `grounding_supported` and playbook recommendations |
| Database readiness | **PASS**; `/ready` returns 200 with database up and 503 with database down. `/health` remains liveness-only. Covered in the frontend API suite. |
| Clean database installation | **PASS**; the schema, migrations, seed, startup, and API were checked in the fresh isolated Compose volume. |

The test run used the dashboard/API code committed in `ab88f5f`. The existing API suite passed, but it does not directly assert the new `incident_bars` response or verify chart rendering in a browser. The chart changes therefore have build and general API-suite coverage, not dedicated automated or visual acceptance evidence.

## 3. Regression history

The restructure initially produced a **16/17** frontend API result. The failing assertion was the chat-widget HVAC question test: the answer did not use stored evidence because `app/api/chat_lite.py` still imported modules from the removed `day2_ahmed/` package. A broad exception concealed the import failure. The imports were corrected to the `app.*` package paths, and the frontend suite then passed. The fix is in commit `740eae7`; the current run on `ab88f5f` passes **18/18**, including the readiness check added later.

## 4. AI verification status

The S1 scenario and unit/integration suites verified the deterministic fallback on `ab88f5f`. The fallback returned `grounding_supported`, verified stored evidence, stated the data freshness limitation, and returned playbook-derived recommendations.

The recorded live Qwen explanation of stored alerts 7 and 8 and the separate fallback response are from the earlier `740eae7` verification (`test_logs/5_explain_live_alert7.json`, `5_explain_fallback_alert7.json`, `5_explain_live.json`, and `5_explain_fallback.json`). They are **historical evidence**, not a live AI pass on `ab88f5f`. Live Ollama inference must be demonstrated again on the tested application revision to close the CEO's live AI item. The Compose `ollama-init` model-pull path was not exercised in the current run.

Grounding validation checks evidence rows, references, values, times, causes, and playbook actions. It does not establish semantic correctness of every sentence produced by a model.

## 5. Remaining items and limits

- Live AI explanation of a stored alert on `ab88f5f`: **not executed** in this run. Historical pass on `740eae7` is preserved separately above.
- Ollama running and pulling its model inside the Compose stack: **not executed**.
- Browser walkthrough recording: **not captured**.
- Playbook approver identity and authorization scope: **requires owner confirmation** before describing all system actions as approved.

## 6. Historical reports and evidence

Older reports and unprefixed logs are retained for traceability and are marked historical. They must not be used as results for `ab88f5f`. The 2026-11-12 results, earlier `103/103` totals, and prior `740eae7` run remain historical snapshots. The current verification summary is [`test_logs/ab88f5f_verification_summary.txt`](../test_logs/ab88f5f_verification_summary.txt).

## 7. Reproduction

Run the commands in the verification summary from the repository root using a fresh Compose project and empty PostgreSQL volume. The summary includes the local port-isolation override used during the run. Keep the target commit and `git status --porcelain` alongside any new test evidence.
