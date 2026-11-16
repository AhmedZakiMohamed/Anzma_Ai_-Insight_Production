# Day 2 Test Results

**Date:** 2026-11-16 (Africa/Cairo)

## Baseline and working tree

- Candidate baseline branch: `import/core-baseline-87e084d`.
- Candidate baseline commit: `73baaf71b47123bdac74b17eb5592183aa999ca0`.
- Source submission reference recorded in the import commit: `87e084d753bcc478b20898b9b9507a2013955ceb`.
- The candidate baseline has local, uncommitted model-default edits in `README.md`, `app/chat_lite.py`, and `day2_ahmed/ai_explainer.py`.
- The tested running service's exact commit/configuration was not established, so endpoint results cannot be attributed to the candidate commit with certainty.

## Commands and checks

| Command or check | Result | Notes |
|---|---|---|
| `GET /health` | **Pass** | HTTP 200, status `ok`. |
| `GET /dashboard/overview` | **Pass** | HTTP 200 with system tiles and KPI cards. |
| `GET /dashboard/summary` | **Pass** | HTTP 200 with KPI values. |
| `GET /dashboard/trends` | **Pass** | HTTP 200 with trend buckets and data. |
| `GET /dashboard/equipment-health` | **Pass** | HTTP 200 with asset health rows. |
| `GET /alerts` | **Pass** | HTTP 200; 13 stored alerts observed. |
| `GET /incidents` | **Pass** | HTTP 200 with incident records. |
| `GET /incidents/9/evidence` | **Pass for sampled record** | One evidence record returned as verified; no unverified records in this incident response. |
| `GET /chat/questions` | **Pass** | HTTP 200 with suggested questions. |
| `POST /chat` (`use_llm=false`) | **Pass** | Database-grounded answer returned 13 alerts; `source: DATABASE`, grounding passed. |
| `POST /chat` (`use_llm=true`) | **Fail for live AI criterion** | Response source was `FALLBACK`; not counted as model success. |
| `GET /api/tags` on Ollama | **Pass for model listing only** | `qwen2.5:3b` was listed. |
| `POST /api/chat` on Ollama | **Not verified** | The 100-second request timed out. |
| `py -3.11 day2_ahmed\test_day2_ahmed.py` | **Not run** | Python launcher reported `No installed Pythons found!`. |
| `py -3.11 -m py_compile app\chat_lite.py day2_ahmed\ai_explainer.py` | **Not run** | Could not start because Python 3.11 is unavailable in this environment. |
| `git diff --check` | **Pass** | No whitespace errors reported for the local edits at the time checked. |

## Regressions and blockers

- No regression suite ran, so regression status is **not verified**.
- Live Qwen inference is not verified. The integrated app returned fallback, and direct Ollama inference timed out.
- Docker was not opened or restarted because doing so crashes the laptop.
- Runtime endpoint evidence is from the active local service, whose exact Git commit is unknown.
- The uncommitted Qwen default edits require Python syntax and regression checks before they can be called verified.

## At-home rerun checklist

Record fresh results below. Do not change the statuses above to **Pass** until the command completes successfully and its actual output is saved.

```powershell
# From the production repository root, after Docker is running:
docker compose ps
docker compose logs --tail=100 db api
Invoke-RestMethod -Uri http://localhost:8000/health
Invoke-RestMethod -Uri http://localhost:8000/dashboard/overview
Invoke-RestMethod -Uri http://localhost:8000/dashboard/summary
Invoke-RestMethod -Uri http://localhost:8000/alerts
Invoke-RestMethod -Uri http://localhost:8000/incidents

# Regression suites documented by this repository:
docker compose exec -T api python day2_ahmed/test_day2_ahmed.py
docker compose exec -T api python test_frontend_api.py
docker compose exec -T api python day2_ahmed/test_day2_integration.py
```

The integration/API suites use dedicated test databases according to the README; confirm the test database names and current test setup before running them. For live AI, send a separate `POST /chat` with `use_llm: true`, save the response, and mark it Pass only if the response identifies a real model result rather than `FALLBACK`.

### Fresh rerun results

- Date/time and machine: ____________________
- Exact commit and branch: ____________________
- Environment/configuration (do not include secrets): ____________________
- Commands and outputs/log locations: ____________________
- Tests passed / failed: ____________________
- Live model response source and model name: ____________________
- Regressions, limitations, blockers, and owners: ____________________

## Owners

- **Amr:** restore a safe test path for Python/application regression checks; identify the running service's source revision; investigate the live model timeout without opening Docker on the unstable laptop.
- **Ahmed:** review the sampled evidence result and database/data-correctness implications.
