> **HISTORICAL - superseded.** Latest authoritative results (tested commit 441110d): [DAY2_TEST_RESULTS.md](DAY2_TEST_RESULTS.md). Kept for history only.

# Baseline Test Report — Anzma AI Insight Core

**Baseline source:** Submission repository, `final` branch
**Reviewed commit:** `87e084d753bcc478b20898b9b9507a2013955ceb`
**Status:** Partial baseline; full application/deployment acceptance remains unverified

## 1. Summary

The reviewed application contains dashboard, KPI, alert, incident, evidence, assistant, and deterministic-fallback code paths. A fresh run of one subsystem suite passed 25/25 tests.

The complete Docker/PostgreSQL-backed application was not run during this baseline. Docker was unavailable, and PostgreSQL was not accepting connections on the expected local port. Code presence and historical reports therefore do not prove that the full application works end to end.

## 2. Verification results

| Area | Status | Evidence and limits |
|---|---|---|
| `day2_ahmed` integrity/operations suite | **PASS — 25/25** | Fresh command `py -3.11 day2_ahmed/test_day2_ahmed.py`; all 25 reported passing. Covers only that suite's scope. |
| Frontend/API report | **NOT RE-RUN** | Historical repository report says 17/17; not fresh evidence. |
| Chat consistency report | **NOT RE-RUN** | Historical repository report says 31/31; not fresh evidence. |
| Day 2 integration report | **NOT RE-RUN** | Historical report says 43/43; not fresh evidence. |
| Docker deployment | **NOT VERIFIED** | Docker unavailable as a command in baseline environment. |
| PostgreSQL-backed API/dashboard | **NOT VERIFIED** | PostgreSQL did not accept connections at `localhost:5432`. |
| Dashboard/system monitoring | **NOT VERIFIED end to end** | Routes/UI exist; complete DB-backed runtime path was not exercised. |
| Operational KPIs | **NOT VERIFIED end to end** | KPI code/tests exist; running dashboard output was not exercised. |
| Alert/incident processing | **PARTIALLY VERIFIED** | Routes/logic exist and 25-test suite passed; complete application workflow was not run. |
| Evidence retrieval | **NOT VERIFIED end to end** | Routes/logic exist; DB-backed retrieval was not exercised. |
| Deterministic assistant fallback | **PARTIALLY VERIFIED** | Fallback logic/tests exist; integrated request path was not exercised. |
| Integrated Ollama/Qwen | **NOT VERIFIED — OUTSTANDING** | Ollama reachable and `qwen2.5:3b` installed, but no integrated application request was run. |
| General telemetry ingestion/external mapping | **NOT IMPLEMENTED in reviewed API** | No general telemetry endpoint or vendor-device-to-Core-asset mapping interface found. |
| Real building integration/equipment control | **OUT OF SCOPE** | CEO's Week 3 email excludes real building integration; equipment control is not authorized. |

## 3. Reproduction details

From the verified `final` branch checkout:

```powershell
py -3.11 day2_ahmed/test_day2_ahmed.py
```

**Observed result:** 25 tests passed.

Environment observations:

- Docker was unavailable as a command.
- PostgreSQL was not accepting connections on `localhost:5432`.
- Ollama was reachable on `localhost:11434`.
- Installed models included `qwen2.5:3b`, `llama3.2:3b`, and `qwen2.5:0.5b`.

Ollama reachability and model installation do not prove execution through the application.

## 4. Known defects, risks, and gaps

1. No end-to-end deployment evidence; a fresh Docker installation has not been demonstrated.
2. Integrated Qwen request and model-unavailable fallback remain to be run and recorded.
3. Configuration defaults differ across modules, and Compose does not pass every runtime setting consistently.
4. Database access crosses route/dashboard/chat boundaries; both psycopg and psycopg2 paths exist.
5. Two schema copies serve different flows. Ahmed must confirm the canonical model before consolidation.
6. Existing API documentation describes `/api/v1` paths not found in the current implementation.
7. `/health` returns `{"status":"ok"}`; dependency readiness is unverified.
8. Current `POST /events` requires internal `asset_id`; a connector-safe telemetry interface is missing.
9. Some operator-facing routes expose database IDs. Connector contracts must not depend on them; broader API changes require compatibility review.
10. Production authentication/authorization was not verified. Reviewed Socket.IO configuration permits broad CORS and needs a release decision.
11. `chat_api.py` is not registered by current `App.py`; trace imports/runtime use before removal.
12. The temperature/window/occupancy example lacks the complete signal set, ingestion path, mappings, and approved rule in the current application.

## 5. IoT dashboard interpretation

This baseline does not verify a live, continuously connected building dashboard. It does not prove continuous connector-safe ingestion, external identity mapping, latest-value freshness/quality, correct handling of missing/stale signals, or consistency across dashboard, KPI, alert, and evidence paths in a running deployment.

These are productization requirements to address and verify incrementally. Real building connectivity remains outside this sprint.

## 6. Sprint 1 verification plan

- Reproduce the baseline in the agreed environment; record commit, commands, and outputs.
- Start the application/database using the deployment procedure.
- Exercise dashboard/system monitoring, KPIs, alerts, incidents, and evidence through the DB-backed application.
- Exercise deterministic assistant fallback through the integrated API.
- Run an integrated Ollama/Qwen request and capture output/provider information.
- Make Ollama unavailable and verify fallback.
- Restart containers and verify stored data persists.
- Test missing/stale data, duplicate event, invalid configuration, and unavailable database/model cases.
- Record actual outputs, failures, and limitations. Do not mark an item PASS without its evidence.
