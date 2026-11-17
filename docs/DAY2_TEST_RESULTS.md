# Day 2 Test Results — Authoritative Report

This is the authoritative Day 2 verification report. Results below refer to one exact tested commit. Historical logs are retained for traceability and are not current evidence.

## Tested revision and environment

| Item | Result |
|---|---|
| Tested commit | `441110d49ae1914fe5ea8096f723d200d90b5893` (`441110d`) |
| Branch/worktree at test time | `main`; clean before the verification run |
| Date | 2026-11-17 (Africa/Cairo) |
| Environment | MacBook Pro, Apple Silicon, Docker Desktop; PostgreSQL 16 and Python 3.11 in API container |
| Install | Isolated Compose project `anzma-acceptance-441110d`, new PostgreSQL volume; development project/database untouched |
| Services | PostgreSQL and API; Ollama ran on the host for live inference. Local override removed host port bindings and pointed API to host Ollama. |
| Raw evidence | [`test_logs/441110d/`](../test_logs/441110d/) |

Commit `441110d` adds incident-chart API assertions to `tests/integration/test_frontend_api.py` and the local verification override under `test_logs/verification/`. It changes no runtime application code. The tests and live API calls ran against this exact commit. Documentation updates made after verification do not alter application or test code.

## Results

| Check | Result |
|---|---|
| Unit suite, `tests/unit/test_day2_ahmed.py` | **PASS — 25/25** |
| Day 2 integration, `tests/integration/test_day2_integration.py` | **PASS — 43/43** |
| Data integrity, `tests/integration/test_data_integrity.py` | **PASS — 18/18** |
| Frontend API, `tests/integration/test_frontend_api.py` | **PASS — 18/18**, including incident chart range/filter and incident lifecycle assertions |
| Chat/dashboard consistency, `tests/integration/test_chat_consistency.py` | **PASS — 31/31** |
| **Five-suite total** | **PASS — 135/135, zero failures** |
| Schema verification, `scripts/verify_schema.sql` | **PASS — OVERALL OK**; 13 tables, 5 views, migration 005 unique index present |
| Dashboard summary, `tests/integration/test_dashboard.py` | **PASS**; read-only summary with `PYTHONPATH=/app` |
| S1 scenario, `tests/scenarios/e2e_s1.py --build` | **PASS — 14/14**; 14/14 evidence rows verified; deterministic fallback path |
| Fresh install and readiness | **PASS**; new isolated DB volume; `/ready` 200 with DB available and 503 when unavailable; `/health` remains liveness-only |
| Test stack status | **PASS**; API and PostgreSQL reported healthy in `compose_ps.txt` |

The incident chart check directly exercises the API data used by the chart, including time range and system filter behavior. No browser recording or visual walkthrough was captured, so browser-rendering evidence remains unverified.

## Regression item

The restructuring report recorded 16/17 frontend tests. The failing chat-widget HVAC question exposed stale imports in `app/api/chat_lite.py` that still referenced the removed `day2_ahmed/` package; a broad exception hid the import failure. The imports were corrected to the `app.*` package paths in commit `740eae7`. On the tested revision, the frontend API suite passes 18/18, including the later readiness and incident-chart checks. The earlier raw 16/17 log is historical; the current 18/18 result is [`frontend_api.txt`](../test_logs/441110d/frontend_api.txt).

## Live AI and fallback demonstrations

Both calls used stored alert **7** (`HVAC_HIGH_TEMP`, asset **CRAC Unit 1**, system **HVAC**, incident **6**) in the isolated test database.

| Path | Result |
|---|---|
| Live model | **PASS** — `ai_source=llm`, model `qwen2.5:7b`; [`live_explanation.json`](../test_logs/441110d/live_explanation.json) |
| Evidence | **PASS** — 7 checked, 7 verified, 0 unverified; cited readings include the 29.0°C warning threshold and 31.83°C peak |
| Validation | **PASS** — `grounding_supported`, no validation errors |
| Recommended actions | Three playbook-derived actions: `HVAC_VERIFY`, `HVAC_COOLING`, `HVAC_TICKET`; each response item reports approved status |
| Deterministic fallback | **PASS, separately invoked** — `ai_source=fallback`, no model used, `grounding_supported`; [`fallback_explanation.json`](../test_logs/441110d/fallback_explanation.json) |

The live call used the host Ollama service. The Compose `ollama-init` model-pull path was not exercised. The response records Ahmed as playbook owner; CEO/owner confirmation of the authority represented by that label remains open. Grounding validation checks evidence references and supported values/actions; it does not prove every generated sentence is semantically correct.

## Remaining evidence limits

- **Not executed:** Ollama startup and model pull from inside the Compose stack.
- **Not captured:** browser walkthrough recording of the dashboard/chart.
- **Pending owner confirmation:** playbook approver identity and authorization scope.

These limits are reported separately from passed functional checks; do not describe them as completed.

## Historical reports and reproduction

Older reports, including the `ab88f5f` report and unprefixed logs, are historical snapshots. They must not be cited as the current results. [`test_logs/COMMIT.txt`](../test_logs/COMMIT.txt) identifies the tested revision. Raw outputs and response JSON are stored in [`test_logs/441110d/`](../test_logs/441110d/). The captured files preserve results but not the exact historical shell invocations, which limits exact replay of that run. For a reproduction, check out the commit in `test_logs/COMMIT.txt`, start a fresh isolated Compose project with an empty DB volume and the verification override, then run the five suites and schema/S1 checks listed above. Do not use the existing development database for acceptance verification.
