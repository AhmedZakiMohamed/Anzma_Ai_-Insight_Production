# Runtime Verification — Day 2

## Current Verification — Tested Commit `441110d`

**Verification date:** 2026-11-17 (Africa/Cairo)
**Tested commit:** `441110d49ae1914fe5ea8096f723d200d90b5893`
**Branch/worktree at test time:** `main`, clean when verification began
**Environment:** MacBook Pro (Apple Silicon), Docker Desktop; PostgreSQL 16 and Python 3.11 in the API container.
**Installation:** Fresh isolated Compose project `anzma-acceptance-441110d` and new PostgreSQL volume; development project/database untouched.
**Evidence:** [DAY2_TEST_RESULTS.md](DAY2_TEST_RESULTS.md), [`test_logs/COMMIT.txt`](../test_logs/COMMIT.txt), and raw outputs in [`test_logs/441110d/`](../test_logs/441110d/).

Commit `441110d` adds chart API assertions and a local verification override only; no runtime application code changed. Documentation and evidence updates made afterward do not alter the tested application or test code.

### Results

| Check | Result |
|---|---|
| Five suites: unit, integration, integrity, frontend API, chat consistency | **Pass — 135/135**; frontend API 18/18 |
| Incident-chart API | **Pass** — response shape, HVAC system filter, time-window behavior, and incident transition from critical to resolved are asserted |
| Schema | **Pass — OVERALL OK**; 13 tables, 5 views, migration 005 unique index present |
| `/ready` database dependency | **Pass** — 200 with DB available, 503 with DB unavailable; `/health` remains liveness-only |
| S1 pipeline scenario | **Pass — 14/14**; deterministic fallback path |
| Read-only dashboard summary | **Pass** |
| Live stored-alert explanation | **Pass** — alert 7, `qwen2.5:7b`, 7/7 evidence verified, `grounding_supported`, three playbook actions |
| Separate deterministic fallback | **Pass** — `ai_source=fallback`, `grounding_supported` |
| Ollama Compose model-pull path | **Not run**; live inference used host Ollama |
| Browser walkthrough recording | **Not captured** |

Historical unprefixed logs under `test_logs/` and the previous `ab88f5f` report are not output from `441110d`.

### Verification limits

- The grounding validator checks evidence references, values, times, causes, and playbook actions; it does not prove semantic correctness of every model sentence.
- Playbook approver identity and authority still require owner confirmation.
- API chart assertions do not constitute browser-rendering evidence; no walkthrough recording was captured.

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
