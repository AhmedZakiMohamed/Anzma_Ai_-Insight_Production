# Anzma AI Insight – Unified Platform

**Anzma AI Insight** is a locally hosted demo of a smart-building operations platform. It takes a simulated fault all the way to an evidence-backed explanation and recommendation on a dashboard:

```text
Simulated fault → Alert detection → Incident + evidence → AI explanation
                → Grounding checks → Recommendation → Dashboard
```

It covers five building systems: **HVAC, Networking, Access Control, CCTV and Fire Alarm**.

> **Status:** integration/demo build on simulated data. It is not a production building-management system. There is no authentication, no live device connection and no autonomous control. AI only explains facts and playbook actions that are already stored; it cannot create readings, faults or actions.

## What the platform does

1. Loads reference data and a simulated history into PostgreSQL on first start.
2. Runs 16 configured threshold, anomaly and compound alert rules, at start-up and every `DETECT_INTERVAL` seconds (default 60). Detection is idempotent, so re-running never duplicates alerts or incidents.
3. Groups alerts of the same location within 15 minutes into **incidents** with a lifecycle (open → acknowledged → resolved).
4. Re-reads every piece of evidence from the original fact tables and reports how many rows were verified.
5. Explains an alert with a local Ollama model when requested, or with a deterministic fallback that goes through the same checks and states the limitation.
6. Recommends only conditional **playbook** actions that are supported by the current facts.
7. Shows all of this in a browser dashboard with an assistant widget, and pushes new alerts live over Socket.IO.

```text
Simulated data → PostgreSQL → Alert detection → Incident grouping → Evidence re-verification
                                                                          │
                                                   Ollama ◄── explanation ──► Deterministic fallback
                                                                          │
                                                            Grounding checks
                                                                          │
                                                   Recommendations + Dashboard + Assistant
```

## Technology

| Area | Technology |
|---|---|
| Language / API | Python 3.11, FastAPI, Uvicorn, python-socketio |
| Database | PostgreSQL 16 (drivers: psycopg 3 for the dashboard, psycopg2 for the alert pipeline) |
| AI runtime | Ollama, optional (default model in Docker: `qwen2.5:3b`) |
| Frontend | Plain HTML, CSS and JavaScript, Chart.js (bundled) |
| Deployment | Docker Compose: `db`, `api`, `ollama`, `ollama-init` |

## Quick start (Docker)

Requirements: Docker Desktop (or Docker Engine with Compose v2) and Git.

Run these commands from the repository root:

```bash
cp deploy/examples/.env.example .env    # then set PGPASSWORD (required)
docker compose -f deploy/compose/docker-compose.yml up -d --build
docker compose -f deploy/compose/docker-compose.yml ps
```

- Open **http://localhost:8000** for the dashboard.
- `http://localhost:8000/docs` is the API explorer; `/health` is the liveness check.
- On a **new, empty** database volume, PostgreSQL loads `db/init/anzma_schema_and_seed.sql`, then `db/migrations/003_incidents.sql`, `004_chat_views.sql`, and `005_alert_uniqueness.sql` automatically. No manual SQL is needed.
- Without the model (first minutes while it downloads, or no internet) everything still works: explanations use the deterministic fallback and say so. Check the download with `docker compose -f deploy/compose/docker-compose.yml logs ollama-init`.
- To skip Ollama entirely: `docker compose -f deploy/compose/docker-compose.yml up -d --build db api`.

Reset to a clean seeded database:

```bash
docker compose -f deploy/compose/docker-compose.yml down -v
docker compose -f deploy/compose/docker-compose.yml up -d --build
```

> **Warning:** `docker compose -f deploy/compose/docker-compose.yml down -v` permanently deletes the local PostgreSQL data and the downloaded Ollama models. The seed scripts only run on an empty volume, so use this after any seed change.

## Demo walkthrough (scenario S1: server-room cooling failure)

1. **Dashboard:** show the HVAC tile and CRAC Unit 1 in *Equipment health*. Status reflects the latest fault/recovery state; a recovered asset clears current Critical status, while its stored critical event and alert/incident history remain visible.
2. **Trends:** the HVAC temperature chart shows the CRAC running about 9 °C above normal for 90 minutes.
3. **Incidents:** open "HVAC temperature above threshold – CRAC Unit 1". The tag *Evidence verified in database 14 / 14* means every evidence row was re-read from `telemetry_records` and matched.
4. **Live model path:** ensure Ollama and the configured Qwen model are ready, tick *Use local AI model*, and click *Explain*. Record the source and model shown by the application. A timeout or fallback is not a live-model pass.
5. **Fallback path:** untick *Use local AI model* and explain again, or stop Ollama and retry. Confirm the deterministic source, evidence verification, grounding result, limitations and playbook actions.
6. **Acknowledge / Resolve** the incident: its incident lifecycle changes independently of the asset's physical recovery state.
7. **Assistant widget:** ask "Why is CRAC 1 temperature high?" or "What is wrong with the building?" (English or Arabic), then compare status and critical counts with the dashboard.

The data window ends at the latest stored record (`as_of`), not the wall clock, and the seed is relative to when it was loaded, so the CRAC fault appears about 20 hours before the latest record. More detail is in `RUN_DEMO.md`.

## Sample data

| Data | Seeded |
|---|---:|
| Buildings | 2 (Main Tower, Data Center Annex) |
| Locations | 6 |
| Assets | 12 |
| System types | 5 |
| Metric types | 8 |
| Event types | 12 |
| Telemetry | about 78 hours at 15-minute intervals |
| Heartbeats | hourly, with a configured camera-offline gap |

All data is synthetic.

## Windows PowerShell: terminal commands

These are reproducible project commands, not a transcript of every command used during development.

**1. Clone or update**

```powershell
Set-Location .\Anzma-Platform-Production
```

**2. Configure the environment**

```powershell
Copy-Item deploy/examples/.env.example .env
notepad .env        # set PGPASSWORD; never commit .env
```

**3. Start the stack**

```powershell
docker compose -f deploy/compose/docker-compose.yml up -d --build
docker compose -f deploy/compose/docker-compose.yml ps
docker compose -f deploy/compose/docker-compose.yml logs --tail=100 db api
Invoke-RestMethod -Uri http://localhost:8000/health
Start-Process "http://localhost:8000"
```

**4. Try the API**

```powershell
Invoke-RestMethod -Uri "http://localhost:8000/dashboard/overview"

$body = @{ message = "Why is CRAC 1 temperature high?"; use_llm = $false } | ConvertTo-Json
Invoke-RestMethod -Uri "http://localhost:8000/chat" -Method Post -ContentType "application/json" -Body $body

# run detection now (safe to repeat)
Invoke-RestMethod -Uri "http://localhost:8000/alerts/run" -Method Post
```

**5. Check Ollama**

```powershell
docker compose -f deploy/compose/docker-compose.yml logs --tail=100 ollama-init
docker compose -f deploy/compose/docker-compose.yml exec ollama ollama list
```

**6. Run the tests** (they need a real PostgreSQL; the user must be allowed to create databases, which `postgres` is)

```powershell
docker compose -f deploy/compose/docker-compose.yml exec -T api python tests/integration/test_frontend_api.py
docker compose -f deploy/compose/docker-compose.yml exec -T api python tests/integration/test_chat_consistency.py
docker compose -f deploy/compose/docker-compose.yml exec -T api python tests/integration/test_day2_integration.py
docker compose -f deploy/compose/docker-compose.yml exec -T api python tests/unit/test_day2_ahmed.py
```

The offline tests also run on the host without any database:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python tests\unit\test_day2_ahmed.py
```

S1 evidence run (builds a separate database named `anzma_e2e`):

```powershell
$env:PGHOST = "localhost"; $env:PGPORT = "5432"; $env:PGUSER = "postgres"
$env:PGPASSWORD = "your-local-password"
python tests\scenarios\e2e_s1.py --build    # add --llm to try Ollama
```

> **Database safety:** `--build` drops and recreates `anzma_e2e`, and the integration test recreates `anzma_day2_test`. Use these only for the dedicated test databases.

**7. Stop**

```powershell
docker compose -f deploy/compose/docker-compose.yml down
docker compose -f deploy/compose/docker-compose.yml down -v
```

**Optional: run the API on Windows against the Docker database.** Stop the Compose `api` first so port 8000 is free, set `PGHOST=localhost` in `.env`, then:

```powershell
docker compose -f deploy/compose/docker-compose.yml up -d db
python -m uvicorn app.main:asgi_app --reload --host 127.0.0.1 --port 8000
```

## HTTP API

Implemented in `app/main.py`, `app/api/frontend_api.py` and `app/api/chat_lite.py`. Full request and response shapes are in the interactive docs at `/docs`.

| Method | Route | Purpose |
|---|---|---|
| GET | `/`, `/dashboard` | Browser dashboard |
| GET | `/health` | Liveness only; it does **not** check the database |
| GET | `/dashboard/filters` | Available buildings, systems and data coverage |
| GET | `/dashboard/summary` | The eight KPI definitions and values (KPI engine shape) |
| GET | `/dashboard/overview` | System tiles and KPI cards used by the page |
| GET | `/dashboard/trends` | Time series for the charts |
| GET | `/dashboard/equipment-health` | One health row per asset |
| GET | `/alerts`, `/alerts/{id}` | Stored alerts, newest first, with evidence |
| GET | `/incidents`, `/incidents/{id}` | Incidents |
| GET | `/incidents/{id}/evidence` | Evidence rows and their verification result |
| POST | `/incidents/{id}/status` | Acknowledge or resolve an incident |
| POST | `/alerts/{id}/explain?use_llm=` | Explanation, grounding checks and recommendations |
| POST | `/alerts/run` | Run detection now |
| POST | `/alerts`, `/events` | Store an alert or event by hand |
| POST | `/ask` | Narrow HVAC/CRAC question route (HTTP 501 for other topics) |
| POST | `/chat`, GET `/chat/questions` | Assistant widget backend |
| Socket.IO | `new_alert` | Live push of newly stored alerts |

The eight KPIs are: monitored assets, healthy assets %, alerts raised, critical alerts, alert rate per hour, average zone temperature, average packet loss and heartbeat compliance.

`/dashboard/summary` accepts `system_type` (`HVAC`, `NETWORKING`, `ACCESS_CONTROL`, `CCTV`, `FIRE_ALARM`), `building_id`, and UTC `start_time` / `end_time`. The interval is start-inclusive, end-exclusive, and limited to 31 days.

**About `/chat` and the model:** the assistant tries Ollama by default (`CHAT_USE_LLM=true`) and falls back to the deterministic path if the model is unavailable. Set `use_llm` to `false` in the request, or `CHAT_USE_LLM=false`, to force the deterministic path.

### What the explanation status means

The successful status is `grounding_supported`, not "validated". It means the implemented grounding checks passed: citations, numbers, times, evidence references, supported causes and approved playbook actions were checked against the stored evidence. It is **not** a guarantee that the reasoning is semantically correct, because no independent semantic validation stage exists yet.

## Repository structure

```text
Anzma-Platform-Production/
├── app/main.py                  # FastAPI app, routes, Socket.IO, detection loop
├── app/api/                     # dashboard, chat and frontend API routes
├── app/domain/                  # alerts, incidents, evidence, AI and operations
├── app/adapters/                # PostgreSQL and Ollama adapters
├── app/repositories/            # KPI/dashboard queries
├── app/config/                  # reference data
├── app/dashboard/static/        # dashboard, chat widget, styles and Chart.js
├── db/init/                     # authoritative runtime schema and seed
├── db/migrations/               # numbered SQL migrations
├── tests/{unit,integration,scenarios}/
├── deploy/compose/              # Dockerfile and Compose configuration
├── deploy/examples/             # safe environment example
├── docs/                        # architecture, verification and handover records
└── legacy/                      # retained old-schema API modules
```

`data-model.sql` is an earlier draft, not the schema the database is built from. The schema that runs is `db/init/anzma_schema_and_seed.sql`.

## Verification status (2026-11-12)

| Suite | Latest result | Notes |
|---|---:|---|
| Frontend API tests | **17/17 passed** | Docker run; includes fallback, recovered-asset status and explicit Unknown/no-data cases. See `TEST_RESULTS_FRONTEND_API.txt`. |
| Chat/dashboard consistency | **31/31 passed** | Five systems, counts, labels, critical KPI, citations and status wording. See `TEST_RESULTS_CHAT_CONSISTENCY.txt`. |
| Offline tests | 25/25 recorded | Previous recorded run; not rerun in this verification. |
| PostgreSQL Day 2 integration tests | 43/43 recorded | Previous reported run; not rerun in this verification. See `TEST_RESULTS_DAY2.txt`. |

The previous API image run used the former entry point. Its recorded HTTP checks do not verify the current moved tree. The integrated output logged Ollama timeouts, so live Qwen inference has **not** been demonstrated as passing. The fallback path was reported as passing. The current tree still requires a fresh execution check.

The repository is ready for the teammate to run from this folder with the intended `.env` and Compose setup. The recorded API tests used the local Compose PostgreSQL service.

## Known limitations

- **Simulated data only.** No live devices or external building-management systems.
- **Playbook approval record.** Amr reports that the engineering owner approved the HVAC playbook. Confirm whether sign-off covers the other system playbooks before presenting their recommendations as approved.
- **Live-model demonstration outstanding.** The current test run recorded Ollama timeouts. Capture a successful integrated Qwen run separately from fallback before claiming live inference passed.
- **Bandwidth units.** `bandwidth_util` is stored as a percentage, but the bandwidth alert thresholds in `alert_config.py` (900 and 980) look like Mbps values. Treat bandwidth alerts as unreliable until the units are reconciled. The chart was relabelled to "utilisation (%)".
- **API contract mismatch.** `API_CONTRACT.md` and the submission document describe a proposed `/api/v1` interface (batch ingestion, `/api/v1/assistant/query`). It is **not implemented**. Use the HTTP API section above and `/docs` as the source of truth.
- **No authentication.** The API, database and Ollama ports are published by Compose, and Socket.IO allows any origin. Do not expose the stack to an untrusted network.
- **Narrow assistant.** `/chat` and `/ask` answer a limited set of grounded questions, mainly building status, alert and camera counts, and HVAC/CRAC. A general tool router is a later step.
- **Grounding, not semantic validation.** See the status explanation above.
- **Model default:** Docker Compose, `.env.example`, `ai_explainer.py`, and `chat_lite.py` use `qwen2.5:3b` when `OLLAMA_MODEL` is unset. Set `OLLAMA_MODEL` in `.env` when selecting another installed model.
- **Incidents stay open** until someone acknowledges or resolves them, even if the physical condition ended. A resolved incident is final; a later fault opens a new one.
- **Limited signals.** The data has no fan state, filter fault, battery, motion or access-granted signals, and access-denial events carry no reason, so related explanations are marked `partial` data sufficiency.
- **`/health` is liveness only.** It does not verify PostgreSQL or Ollama.
- **Socket.IO is loaded from a CDN.** Offline, the alert center still loads from `/alerts` but live push shows "offline".
- **Two database drivers.** The operational adapter uses psycopg2 while dashboard queries use psycopg 3. Unifying them is planned cleanup.
- **Legacy files.** `chat_engine.py`, `chat_api.py` and `dashboard_api.py` in the repository root belong to the old schema and are unused.

## Team workflow

| Rule | Detail |
|---|---|
| Branches | `main` holds the earlier integration build; `final` is the submission build. Feature branches use `feat/<module>-<owner>`. |
| Reviews | The other engineer reviews every pull request. |
| Database changes | Always a new numbered migration (`004_...sql`); never edit an applied one. |
| Secrets | `.env` is git-ignored. Never commit it. |

## Design documents

[Architecture](ARCHITECTURE.md) · [Data model](DATA_MODEL.md) · [Decision matrix](DECISION_MATRIX.md) · [API contract (proposed)](API_CONTRACT.md) · [Integration plan](INTEGRATION_PLAN.md) · [Run and demo guide](RUN_DEMO.md) · [Day 2 handover](DAY2_AHMED_HANDOVER.md) · [Test results](TEST_RESULTS_DAY2.txt)

## Authors

Ahmed and Amr – Anzma Tech.
