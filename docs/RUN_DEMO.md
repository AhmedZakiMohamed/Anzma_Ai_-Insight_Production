# Run the Anzma AI Insight demo

This guide starts the local Docker demo and walks through how the dashboard connects building assets, alerts, incidents, and health charts. The platform uses seeded or submitted observations; it does not connect to or control physical building equipment. See the [project README](../README.md) for the system architecture and data model.

## 1. Start the platform

From the repository root, create a local environment file from the example and set a non-empty PostgreSQL password:

```bash
cp deploy/examples/.env.example .env
# Edit .env and replace PGPASSWORD=change-me with a local password.
```

Start or rebuild the application:

```bash
docker compose --env-file .env -p anzma-insight -f deploy/compose/docker-compose.yml up -d --build
docker compose --env-file .env -p anzma-insight -f deploy/compose/docker-compose.yml ps
```

Open [http://localhost:8000](http://localhost:8000) for the dashboard. The API is available at [http://localhost:8000/docs](http://localhost:8000/docs), and [http://localhost:8000/health](http://localhost:8000/health) reports basic API health.

Compose requires `PGPASSWORD`. If it reports that the variable is missing, confirm `.env` exists in the repository root, contains a non-empty `PGPASSWORD`, and that the command includes `--env-file .env`.

On the first start with an empty PostgreSQL volume, Compose loads the schema and seed, then applies the incident, chat-view, and alert-uniqueness migrations. These initialization scripts do not rerun on an existing volume. Normal restarts preserve the database. Avoid `docker compose down -v` unless you intend to delete the local PostgreSQL data and downloaded Ollama models.

Alert detection runs at startup and periodically afterward (default interval: 60 seconds). The detector is idempotent. Ollama downloads the configured model on first startup; until it is ready, explanations can use the deterministic fallback. Check progress with:

```bash
docker compose --env-file .env -p anzma-insight -f deploy/compose/docker-compose.yml logs ollama-init
```

The compose services and environment requirements are defined in [`deploy/compose/docker-compose.yml`](../deploy/compose/docker-compose.yml) and [`deploy/examples/.env.example`](../deploy/examples/.env.example).

## 2. Follow an observation through the dashboard

The dashboard reads the same stored asset, telemetry, event, alert, and incident records through several API views. The main endpoints and their roles are:

| Dashboard area | Endpoint | What it represents |
|---|---|---|
| System tiles, KPIs, System health chart, Incidents by asset chart | `GET /dashboard/overview` | System and asset health, event-based KPIs, and incident lifecycle counts. |
| Trend charts | `GET /dashboard/trends` | Telemetry and event observations in the selected time window. |
| Equipment health | `GET /dashboard/equipment-health` | Per-asset status and last-seen information. |
| Alert Center | `GET /alerts`; Socket.IO `new_alert` | Active alert records and newly detected alerts pushed to connected dashboards. |
| Incidents and evidence | `GET /incidents`, `GET /incidents/{id}/evidence` | Grouped operator work items and the underlying recorded evidence. |
| Explain alert | `POST /alerts/{id}/explain` | Model-assisted or deterministic, evidence-grounded explanation. |
| Change incident status | `POST /incidents/{id}/status` | Acknowledge or resolve an incident and update its linked alerts. |

The route wiring is in [`app/main.py`](../app/main.py), dashboard responses are assembled in [`app/api/frontend_api.py`](../app/api/frontend_api.py), and the page rendering and refresh behavior are in [`app/dashboard/static/dashboard.html`](../app/dashboard/static/dashboard.html).

### What each area means

- **Top filters:** the system filter narrows trends, the health chart, equipment view, and incident chart. System tiles continue to show all systems. The time filter applies to time-windowed trends and health/freshness calculations; incident history is not limited to this window. “Now” is based on the latest stored observation, so a seeded demo can show dates that differ from the computer clock.
- **Alert Center:** lists unresolved alerts (`open` or `acknowledged`). Resolved alerts leave this active list, while their history remains attached to incidents. Use **Open incident** on an alert to jump to its related incident. Socket.IO pushes newly detected alerts; the page also refreshes API-backed views after an operator changes incident status.
- **Incidents:** groups related alert records so an operator can review evidence and manage their lifecycle. The status sequence is `open → acknowledged → resolved`; resolved is final. Changing status updates the incident and its linked alerts together. The lifecycle and grouping rules are in [`app/domain/incidents/incident_engine.py`](../app/domain/incidents/incident_engine.py).
- **System status and System health chart:** both summarize assets, rather than counting alerts or incidents. Green means healthy, amber means warning, red means critical, and gray is used for no-data/unknown categories when present. An active alert or unresolved fault can affect health. Resolving an incident resolves its linked alerts and retires matching fault observations covered by those alerts. A newer fault, stale reporting, or missing telemetry can still leave an asset in Warning or Unknown; resolution does not create a recovery event or heartbeat. Shared health calculations are in [`app/repositories/dashboard_core_pg.py`](../app/repositories/dashboard_core_pg.py) and per-asset health is in [`app/api/frontend_api.py`](../app/api/frontend_api.py).
- **Incidents by asset chart:** counts incident history against each affected asset. Green is resolved history; amber and red are active warning and critical incidents; gray is active info. An incident linked to multiple assets can appear against each. This chart describes incident workflow and severity, so it can differ from the health chart, which describes current asset condition. Its API data is `incident_bars` in `GET /dashboard/overview`.
- **Trend charts:** show observations over the selected time window. Events by system excludes routine heartbeats. HVAC temperature and network utilization use telemetry; denied access uses stored denial events. The seed does not include access-granted observations, so the dashboard does not invent a zero series.
- **KPI cards:** “Alerts raised” and “Critical alerts” count warning/critical event rows in the selected fact-data window. They are not counts of active Alert Center rows. “Open incidents” counts incidents that are not resolved across stored history, regardless of the selected time window.

The database schema, seeded assets, and compatible observation types are documented in [`db/init/anzma_schema_and_seed.sql`](../db/init/anzma_schema_and_seed.sql). The health chart logic is rendered by the dashboard page; trend calculations are provided through `GET /dashboard/trends`.

## 3. Demo walkthrough: trace an HVAC fault

The seed includes **CRAC Unit 1** with HVAC telemetry and a cooling-fault scenario. The seed data is relative to the time it was loaded.

1. Select **All systems** and a time window that includes the seed observations. Note the HVAC system tile, the asset counts in System health, the incidents list, and the alert count.
2. Select **HVAC** in the system filter. Review the HVAC temperature trend and the **System health** and **Incidents by asset** charts. They answer different questions: asset condition versus incident history.
3. Open the HVAC temperature incident for CRAC Unit 1. Inspect its evidence and recorded time range. When the UI reports evidence verification, it means the evidence rows were checked against the database; it does not mean the underlying simulated condition was physically inspected.
4. In the incident details, choose **Acknowledge**. The incident moves to acknowledged and its linked alerts follow. It remains active in the Alert Center, and its asset may remain Warning or Critical.
5. Choose **Resolve**. The incident remains in history with a green Resolved state, while its linked alerts leave the active Alert Center. The dashboard refreshes incident and alert views, system health, and charts. If there is a newer fault or stale/missing asset data, health can still show Warning or Unknown after resolution.
6. Select **All systems** again and confirm the views return to the building-wide scope.

The seeded asset and event examples span HVAC, Network, Access control, CCTV, and Fire alarm. System-specific names and observation pairs are listed in the [README system table](../README.md#systems-and-their-assets). For example, the seed includes `link_down` / `link_up` for Network and `camera_offline` / `camera_online` for CCTV. Not every event type has a paired recovery event; inspect the incident evidence and the seeded event catalogue before presenting a scenario as recovered.

### Optional AI explanation

Enable **Use local AI model (Ollama)** in the incident panel and request an explanation. Note which source/model the application reports. A timeout, unavailable model, or fallback response is not a successful live-model demonstration. Disable the option or make Ollama unavailable and request an explanation again to demonstrate the deterministic fallback. Both modes should be reviewed against the stored evidence; model output does not control building equipment.

## 4. Troubleshooting during the demo

| Symptom | Check |
|---|---|
| Compose says `PGPASSWORD` is missing | Ensure repository-root `.env` contains a non-empty password and rerun with `--env-file .env`. Compose interpolates this value before starting containers. |
| Dashboard shows old frontend or API behavior after code changes | Rebuild the relevant service with the same compose command (`up -d --build api` for API changes). Hard-refresh the browser for static dashboard changes. |
| Alert Center says live/offline or a new alert does not appear immediately | Check API health and the browser connection. The Alert Center still fetches stored alerts from `/alerts`; Socket.IO is the live push path. Detection runs at its configured interval. |
| Incident resolves but an asset remains Warning/Unknown | Review the asset's latest observations, evidence, freshness, and any newer fault. Resolution retires matched fault observations covered by the linked alerts, but it does not synthesize telemetry, a heartbeat, or a recovery event. |
| Seed data does not appear after a restart | Initialization scripts run only when PostgreSQL starts with an empty data volume. Existing volumes are not reseeded automatically. |
| AI explanation falls back or times out | Check `ollama-init` logs and whether the configured model is ready. The non-AI dashboard and deterministic explanation path can operate while Ollama is unavailable. |

## 5. Optional integration checks

These commands are available for maintainers who want to run the PostgreSQL-backed checks. The API container must use a database role allowed to create test databases. These are commands to run; this guide does not assert that they have been run against your current checkout.

```bash
docker compose --env-file .env -p anzma-insight -f deploy/compose/docker-compose.yml exec -T api python tests/integration/test_frontend_api.py
docker compose --env-file .env -p anzma-insight -f deploy/compose/docker-compose.yml exec -T api python tests/integration/test_chat_consistency.py
docker compose --env-file .env -p anzma-insight -f deploy/compose/docker-compose.yml exec -T api python tests/integration/test_day2_integration.py
docker compose --env-file .env -p anzma-insight -f deploy/compose/docker-compose.yml exec -T api python tests/unit/test_day2_ahmed.py
docker compose --env-file .env -p anzma-insight -f deploy/compose/docker-compose.yml exec -T api python tests/scenarios/e2e_s1.py --build --llm
```

The scenario script exercises the data pipeline; it does not replace the browser walkthrough above. A passing fallback check also does not establish that live Ollama inference succeeded.

## Source files

- [`app/main.py`](../app/main.py) — app routes, startup detection, and Socket.IO broadcasts.
- [`app/services/pipeline.py`](../app/services/pipeline.py) — detection-to-storage workflow.
- [`app/domain/alerts/alert_engine.py`](../app/domain/alerts/alert_engine.py) — alert rule evaluation and persistence.
- [`app/domain/incidents/incident_engine.py`](../app/domain/incidents/incident_engine.py) — incident grouping and lifecycle transitions.
- [`app/repositories/dashboard_core_pg.py`](../app/repositories/dashboard_core_pg.py) — shared dashboard summaries and asset-health calculations.
- [`app/api/frontend_api.py`](../app/api/frontend_api.py) — dashboard, trend, incident-chart, and equipment-health API responses.
- [`app/dashboard/static/dashboard.html`](../app/dashboard/static/dashboard.html) — filters, charts, incident actions, and page refreshes.
- [`db/init/anzma_schema_and_seed.sql`](../db/init/anzma_schema_and_seed.sql) — database schema and synthetic seed data.
- [`deploy/compose/docker-compose.yml`](../deploy/compose/docker-compose.yml) — local database, API, and Ollama services.
