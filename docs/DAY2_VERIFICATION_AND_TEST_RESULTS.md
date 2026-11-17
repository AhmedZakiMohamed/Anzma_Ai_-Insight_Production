> **HISTORICAL - superseded.** Latest authoritative results (tested application commit ab88f5f): [DAY2_TEST_RESULTS.md](DAY2_TEST_RESULTS.md). Kept for history only.

# Day 2 Runtime Verification and Test Results

**Date:** 2026-11-16 (Africa/Cairo)  
**Application checked:** Anzma AI Insight Core at `http://localhost:8000`  
**Method:** HTTP requests against the already running app. Docker was not opened, and no services were restarted.

## Baseline and environment

- Local candidate branch: `import/core-baseline-87e084d`.
- Local candidate commit: `73baaf71b47123bdac74b17eb5592183aa999ca0`.
- Source submission reference: `87e084d753bcc478b20898b9b9507a2013955ceb`.
- Local checkout has uncommitted edits to `README.md`, `app/chat_lite.py`, and `day2_ahmed/ai_explainer.py`, aligning the default Ollama model with `qwen2.5:3b`.
- **Not verified:** the running service's source commit and whether it includes those edits. Results below prove behavior of the active service only.
- Ollama's tags endpoint listed `qwen2.5:3b`; model listing alone does not prove inference.

## Runtime results

| Check | Result | Evidence |
|---|---|---|
| Liveness | **Pass** | `GET /health` returned HTTP 200, `{"status":"ok"}`. |
| Dashboard/system status | **Pass** | `GET /dashboard/overview` returned HTTP 200 with a 24-hour window, system tiles, and KPI cards. |
| Operational KPIs | **Pass** | `GET /dashboard/summary` returned HTTP 200 with KPI values. |
| Trends | **Pass** | `GET /dashboard/trends` returned HTTP 200 with time buckets and series. |
| Equipment health | **Pass** | `GET /dashboard/equipment-health` returned HTTP 200 with asset status and last-seen timestamps. |
| Alerts | **Pass** | `GET /alerts` returned HTTP 200 and 13 stored alerts. |
| Incidents | **Pass** | `GET /incidents` returned HTTP 200 with incident records. |
| Evidence retrieval/verification | **Pass for sampled incident** | `GET /incidents/9/evidence` returned HTTP 200 with one verified evidence record and no unverified records in that response. Not exhaustive across all evidence. |
| Assistant deterministic fallback | **Pass** | `POST /chat` with `use_llm: false` answered that there are 13 alerts; response showed `source: DATABASE` and `grounding.passed: true`. |
| Assistant live Qwen path | **Fail for live AI acceptance** | `POST /chat` with `use_llm: true` returned `source: FALLBACK`. This is not live model success. |
| Direct Ollama inference | **Not verified; timed out** | A direct request to `POST http://localhost:11434/api/chat` timed out after 100 seconds. |
| API docs | **Pass** | `GET /docs` returned HTTP 200. |
| Database readiness | **Partial** | Database-backed endpoints returned data. `/health` is documented as liveness only; a distinct DB readiness result was not checked. |

### Follow-up check: detection and current service state

- A `POST /alerts/run?hours=24` attempt was refused at `localhost:8000` before an HTTP response was received. The detection command therefore **did not run**, and no alert or incident changes from this attempt are reported.
- A subsequent read-only check found ports 5432 (PostgreSQL) and 8000 (FastAPI) not listening. Port 11434 (Ollama) was listening.
- This happened after the earlier HTTP requests had returned 200. The earlier endpoint results remain valid for the time they were collected, but the application is not currently available for further tests.
- Alert detection and incident state transitions are **not verified** in this run. No incident was acknowledged or resolved, to preserve the existing dataset.

## Regression test results

| Command/check | Result | Notes |
|---|---|---|
| `py -3.11 day2_ahmed\test_day2_ahmed.py` | **Not run** | Python launcher reported `No installed Pythons found!`. |
| `py -3.11 -m py_compile app\chat_lite.py day2_ahmed\ai_explainer.py` | **Not run** | Python 3.11 was unavailable in this environment. |
| `git diff --check` | **Pass** | No whitespace errors reported for the local edits at the time checked. This is not an application regression test. |

Regression status is therefore **not verified**. No live-model or full-stack result is claimed as passed.

## At-home rerun record

Fill this section from fresh execution before changing any result above to Pass. Save the actual output/logs and test the exact commit listed below.

- Run date/time and machine: ____________________
- Branch and exact Git commit: ____________________
- Working tree clean (`git status --short`): yes / no
- Environment/configuration (omit secrets): ____________________
- Docker services (`docker compose ps`): ____________________
- Service logs (`docker compose logs --tail=100 db api`): ____________________
- Dashboard, KPI, alert, incident, and evidence outputs: ____________________
- Deterministic assistant output and `source`: ____________________
- Live assistant output, `source`, and model name: ____________________
- Regression commands and passed/failed counts: ____________________
- Regressions, blockers, limitations, and owners: ____________________

Suggested checks after the runtime is available:

```powershell
docker compose ps
docker compose logs --tail=100 db api
Invoke-RestMethod -Uri http://localhost:8000/health
Invoke-RestMethod -Uri http://localhost:8000/dashboard/overview
Invoke-RestMethod -Uri http://localhost:8000/dashboard/summary
Invoke-RestMethod -Uri http://localhost:8000/alerts
Invoke-RestMethod -Uri http://localhost:8000/incidents
docker compose exec -T api python day2_ahmed/test_day2_ahmed.py
docker compose exec -T api python test_frontend_api.py
docker compose exec -T api python day2_ahmed/test_day2_integration.py
```

Confirm the test database names and setup before running the API/integration suites. Count live AI as a pass only when the saved app response shows a real model result (for example `source: OLLAMA`), not `FALLBACK`.

## Outstanding items and owners

- **Amr:** identify the running service's source revision; rerun Python and integration regression suites; investigate the model timeout in a stable environment.
- **Ahmed:** review evidence/data-correctness results and confirm the database-backed behavior.
- Docker was not opened or restarted because it crashes the laptop. No service or repository files were overwritten during these checks.
