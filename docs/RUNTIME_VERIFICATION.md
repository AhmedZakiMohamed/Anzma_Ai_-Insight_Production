# Runtime Verification — Day 2

## Current Verification — Commit `740eae7`

**Verification date:** 2026-11-17 (Africa/Cairo)  
**Tested commit:** `740eae72bafaf112f373cbdd8802a66b04de5004`  
**Branch:** `import/core-baseline-87e084d`  
**Machine:** MacBook Pro (Apple Silicon), Docker Desktop  
**Target:** Anzma AI Insight Core — `http://localhost:8000`  
**Installation:** Fresh Docker Compose project `anzma-clean` with an empty database volume.  
**Model path:** Ollama running natively on the Mac (`qwen2.5:7b`), accessed through `docker-compose.override.yml` using `OLLAMA_HOST=http://host.docker.internal:11434`.

**Commit evidence:** `test_logs/COMMIT.txt`  
**Full test report:** `DAY2_TEST_RESULTS.md`

### Source and environment

- The current verification targets commit `740eae72bafaf112f373cbdd8802a66b04de5004`.
- The application was tested using a fresh Compose project named `anzma-clean` with an empty database volume.
- Ollama was running natively on the Mac, using model `qwen2.5:7b`.
- The API accessed Ollama through the local Compose override.
- The evidence files listed below are retained under `test_logs/`.

### Results

| Check | Result | Evidence |
|---|---|---|
| Services started (`docker compose ps`) | **Pass** | `test_logs/2_ps.txt` |
| Application liveness (`GET /health`) | **Pass** — HTTP 200; liveness only | `test_logs/2_health.txt` |
| Dashboard overview, summary, alerts and incidents | **Pass** — HTTP 200 | `test_logs/2_endpoints.txt` |
| Database schema verification | **Pass** — `OVERALL OK` | `test_logs/1_verify_schema.txt` |
| Assistant live-model path (`use_llm=true`) | **Pass** — `source: OLLAMA` | `test_logs/4_chat_live.json` |
| Live explanation of a stored alert | **Pass** — `ai_source: llm`, `model_used: qwen2.5:7b` | `test_logs/5_explain_live_alert7.json` |
| Model loaded in Ollama | **Pass** | `test_logs/5_ollama_ps.json` |
| Deterministic fallback, tested separately | **Pass** — `ai_source: fallback` | `test_logs/5_explain_fallback_alert7.json` |
| Database readiness distinct from liveness | **Partial** — `/health` does not report database status; Compose waits for the database healthcheck, and database-down returns HTTP 503 on page endpoints (see test results section 6) | `DAY2_TEST_RESULTS.md`, section 6 |
| Ollama running inside a Compose container | **Not executed** — model pull was blocked/stuck on the available network | Not applicable |

### Important implementation note

The earlier commit `72d9bcf` does not start successfully because of:

`ModuleNotFoundError: ai_explainer`

Commit `b26b324` contains the fix for this import issue.

### Verification limitations

- The `/health` endpoint confirms application liveness, not complete database readiness.
- The Compose-based Ollama model-pull path was not verified because the model pull was blocked on the available network.
- The live AI verification used Ollama running natively on the Mac, not an Ollama container.
- The full test report contains the detailed commands and results.

---

# Historical (Superseded): Verification of 2026-11-16

**Status: Historical — not the current verification.**

The following section preserves the previous runtime verification report for traceability. Its results belong to the earlier revision and must not be interpreted as results for commit `740eae7`.

**Verification date:** 2026-11-16 (Africa/Cairo)  
**Target:** Anzma AI Insight Core, accessed at `http://localhost:8000`  
**Method:** Read-only HTTP checks against the already running application. Docker was not opened, and no services were restarted.

## Source and environment

- Local checkout: branch `import/core-baseline-87e084d`, commit `73baaf71b47123bdac74b17eb5592183aa999ca0`.
- The local checkout had uncommitted edits to `README.md`, `app/chat_lite.py`, and `day2_ahmed/ai_explainer.py` to align the default Ollama model with `qwen2.5:3b`.
- **Not verified:** the running service's source commit and whether it included those uncommitted edits. The HTTP checks established behavior of the running service, not its exact source revision.
- Ollama's tags endpoint listed `qwen2.5:3b` as installed. This did not establish that inference succeeded.

## Historical results

| Check | Result | Observed evidence |
|---|---|---|
| Application liveness | **Pass** | `GET /health` returned HTTP 200 with `{"status":"ok"}`. |
| Dashboard and system status | **Pass** | `GET /dashboard/overview` returned HTTP 200 with a 24-hour window, system status tiles, and KPI cards. |
| KPI retrieval | **Pass** | `GET /dashboard/summary` returned HTTP 200 with KPI definitions and values. |
| Trends | **Pass** | `GET /dashboard/trends` returned HTTP 200 with time buckets and series. |
| Equipment health | **Pass** | `GET /dashboard/equipment-health` returned HTTP 200 with per-asset status and last-seen timestamps. |
| Alerts | **Pass** | `GET /alerts` returned HTTP 200 and 13 stored alerts. |
| Incidents | **Pass** | `GET /incidents` returned HTTP 200 with incident records. |
| Evidence retrieval and verification | **Pass for sampled incident** | `GET /incidents/9/evidence` returned HTTP 200: one evidence record, `verified: 1`, and no unverified records. This was a sample check, not exhaustive validation of all evidence. |
| Assistant deterministic path | **Pass** | `POST /chat` with `use_llm: false` answered “There are 13 alerts in the database,” with `source: DATABASE` and `grounding.passed: true`. |
| Assistant live-model path | **Fail for live AI acceptance** | `POST /chat` with `use_llm: true` returned `source: FALLBACK`. A fallback response was not successful live AI evidence. |
| Direct Ollama inference | **Not verified; timed out** | A short request to `POST http://localhost:11434/api/chat` timed out after 100 seconds. The model being listed by `/api/tags` was not inference evidence. |
| API documentation availability | **Pass** | `GET /docs` returned HTTP 200. |
| PostgreSQL and readiness distinction | **Partial** | Database-backed endpoints returned data, confirming database access through those application paths. `/health` was documented as liveness only; a distinct database readiness result was not checked. |

## Historical limitations

- The running service's exact Git commit was unknown.
- Live Qwen inference had not been demonstrated; the integrated assistant fell back and direct inference timed out.
- This report recorded endpoint checks only. It did not claim that the complete acceptance flow or every KPI, alert, incident, or evidence case was verified.
- Docker was not opened or restarted at the user's request because opening Docker crashed the laptop.

## At-home rerun record

The following section is retained as a historical rerun template. Complete it with fresh evidence before changing any result to **Pass**.

- Run date/time and machine: __________________________
- Git branch and exact commit tested: __________________________
- Working tree clean (`git status --short`): yes / no
- Docker services (`docker compose ps`): __________________________
- Database/API logs checked (`docker compose logs --tail=100 db api`): __________________________
- `GET /health`: __________________________
- `GET /dashboard/overview` and `/dashboard/summary`: __________________________
- `GET /alerts`, `/incidents`, and one `/incidents/{id}/evidence`: __________________________
- `POST /chat` with `use_llm=false` and response `source`: __________________________
- `POST /chat` with `use_llm=true` and response `source`: __________________________
- Actual model identifier and evidence of inference: __________________________
- Regression test commands and pass/fail counts: __________________________
- Remaining failures and owner: __________________________

For assistant checks, preserve the returned JSON. Count live AI as a pass only when the application response identifies the model path (for example, `source: OLLAMA`). A `FALLBACK` response is not live AI evidence.