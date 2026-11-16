# Runtime Verification — Day 2

**Verification date:** 2026-11-16 (Africa/Cairo)  
**Target:** Anzma AI Insight Core, accessed at `http://localhost:8000`  
**Method:** Read-only HTTP checks against the already running application. Docker was not opened, and no services were restarted.

## Source and environment

- Local checkout: branch `import/core-baseline-87e084d`, commit `73baaf71b47123bdac74b17eb5592183aa999ca0`.
- The local checkout has uncommitted edits to `README.md`, `app/chat_lite.py`, and `day2_ahmed/ai_explainer.py` to align the default Ollama model with `qwen2.5:3b`.
- **Not verified:** the running service's source commit and whether it includes those uncommitted edits. The HTTP checks establish behavior of the running service, not its exact source revision.
- Ollama's tags endpoint listed `qwen2.5:3b` as installed. This does not establish that inference succeeds.

## Results

| Check | Result | Observed evidence |
|---|---|---|
| Application liveness | **Pass** | `GET /health` returned HTTP 200 with `{"status":"ok"}`. |
| Dashboard and system status | **Pass** | `GET /dashboard/overview` returned HTTP 200 with a 24-hour window, system status tiles, and KPI cards. |
| KPI retrieval | **Pass** | `GET /dashboard/summary` returned HTTP 200 with KPI definitions and values. |
| Trends | **Pass** | `GET /dashboard/trends` returned HTTP 200 with time buckets and series. |
| Equipment health | **Pass** | `GET /dashboard/equipment-health` returned HTTP 200 with per-asset status and last-seen timestamps. |
| Alerts | **Pass** | `GET /alerts` returned HTTP 200 and 13 stored alerts. |
| Incidents | **Pass** | `GET /incidents` returned HTTP 200 with incident records. |
| Evidence retrieval and verification | **Pass for sampled incident** | `GET /incidents/9/evidence` returned HTTP 200: one evidence record, `verified: 1`, and no unverified records. This is a sample check, not exhaustive validation of all evidence. |
| Assistant deterministic path | **Pass** | `POST /chat` with `use_llm: false` answered “There are 13 alerts in the database,” with `source: DATABASE` and `grounding.passed: true`. |
| Assistant live-model path | **Fail for live AI acceptance** | `POST /chat` with `use_llm: true` returned `source: FALLBACK`. A fallback response is not a successful live AI test. |
| Direct Ollama inference | **Not verified; timed out** | A short request to `POST http://localhost:11434/api/chat` timed out after 100 seconds. The model being listed by `/api/tags` is not inference evidence. |
| API documentation availability | **Pass** | `GET /docs` returned HTTP 200. |
| PostgreSQL and readiness distinction | **Partial** | Database-backed endpoints returned data, which confirms database access through those application paths. `/health` is documented as liveness only; a distinct database readiness result was not checked. |

## Limitations

- The running service's exact Git commit is unknown.
- Live Qwen inference has not been demonstrated; the integrated assistant fell back and direct inference timed out.
- This report records endpoint checks only. It does not claim that the complete acceptance flow or every KPI, alert, incident, or evidence case was verified.
- Docker was not opened or restarted at the user's request because opening Docker crashes the laptop.

## At-home rerun record

Fill this section from a fresh run before changing any result above to **Pass**. Keep the output or logs with the report.

- Run date/time and machine: ____________________
- Git branch and exact commit tested: ____________________
- Working tree clean (`git status --short`): yes / no
- Docker services (`docker compose ps`): ____________________
- Database/API logs checked (`docker compose logs --tail=100 db api`): ____________________
- `GET /health`: ____________________
- `GET /dashboard/overview` and `/dashboard/summary`: ____________________
- `GET /alerts`, `/incidents`, and one `/incidents/{id}/evidence`: ____________________
- `POST /chat` with `use_llm=false` and response `source`: ____________________
- `POST /chat` with `use_llm=true` and response `source`: ____________________
- Actual model identifier and evidence of inference: ____________________
- Regression test commands and pass/fail counts: ____________________
- Remaining failures and owner: ____________________

For the assistant checks, preserve the returned JSON. Count live AI as a pass only when the app response identifies the model path (for example `source: OLLAMA`); a `FALLBACK` response is not live AI evidence.
