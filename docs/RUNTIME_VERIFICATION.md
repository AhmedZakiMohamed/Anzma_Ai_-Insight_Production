# Runtime Verification — Day 2

## Current Verification — Tested Application Commit `ab88f5f`

**Verification date:** 2026-11-17 (Africa/Cairo)
**Tested source commit:** `ab88f5f9c9b433b1c2caafa22ec31f02bfda6fda`
**Branch and worktree at test time:** `main`, clean
**Environment:** MacBook Pro (Apple Silicon), Docker Desktop; PostgreSQL 16 and Python 3.11 in the API container.
**Installation:** Fresh isolated Compose project `anzma-ab88-verify` with a new empty PostgreSQL volume. Only DB and API services were started; a local override removed host port bindings.
**Current evidence:** [`DAY2_TEST_RESULTS.md`](DAY2_TEST_RESULTS.md) and [`test_logs/ab88f5f_verification_summary.txt`](../test_logs/ab88f5f_verification_summary.txt).

Commit `5c3ffb9` was created afterward to sync `docs/README.md`. It changed no application, test, deployment, database, script, or dependency files; it is a documentation-only follow-up, not a second tested application version.

### Results

| Check | Result |
|---|---|
| Unit / Day 2 integration / data integrity / frontend API / chat consistency | **Pass — 135/135 total** |
| Schema verification | **Pass — OVERALL OK**, 13 tables, 5 views, migration 005 unique index present |
| `/ready` database dependency | **Pass** — 200 with DB up, 503 with DB down; `/health` remains liveness-only |
| S1 pipeline scenario | **Pass — 14/14**, 14/14 evidence rows verified, deterministic fallback and recommendations |
| Read-only dashboard summary | **Pass**; invoked with `PYTHONPATH=/app` |
| Live Ollama explanation on this source commit | **Not run**; prior live evidence belongs to `740eae7` |
| Ollama Compose model-pull path | **Not run** |

The test summary records the exact commands and the local port-isolation override. Historical unprefixed logs under `test_logs/` belong to earlier runs and are not represented as output from `ab88f5f`.

### Verification limits

- The S1 explanation used the deterministic fallback; this run does not prove live model inference.
- The grounding validator checks supported values, times, references, causes, and playbook actions, but is not a semantic correctness check of model wording.
- No browser walkthrough recording was captured in this run.

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
