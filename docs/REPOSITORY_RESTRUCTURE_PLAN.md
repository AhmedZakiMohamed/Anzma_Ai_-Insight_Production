# Repository Restructure Plan

**Status:** Proposed plan; no restructuring changes are implied by this document
**Scope:** One working repository for Anzma AI Insight Core during this sprint

## 1. Goal

Prepare the current integrated application for modular development while preserving existing behavior. Make changes incrementally, keep them reviewable, and maintain a clear boundary between Core and future vendor integrations.

## 2. Current repository observations

Reviewed source: submission repository `final` branch, commit `87e084d753bcc478b20898b9b9507a2013955ceb`.

- `App.py` is the current entry point and combines startup, API routes, database access, background detection, and real-time alert notification responsibilities. Treat it as compatibility-sensitive.
- Dashboard code includes `app/dashboard_core_pg.py` and `app/frontend_api.py`; operational paths in `App.py` use `day2_connect`. The reviewed application uses psycopg and psycopg2 paths. Choosing one driver alone will not remove coupling; connection/configuration and query ownership need boundaries.
- `App.py` modifies `sys.path` to reach `day2_ahmed`, making imports dependent on repository layout.
- `chat_api.py`, `chat_engine.py`, and `dashboard_api.py` form a separate path. `App.py` does not register `chat_api.py`. Treat this path as legacy until imports and runtime use are checked; do not delete it during initial restructuring.
- `db/init/anzma_schema_and_seed.sql` is the sole schema-and-seed bootstrap. Versioned schema changes live in `db/migrations/`; tests should use the same paths as Compose.
- `day2_ahmed/alert_config.py` contains thresholds, rules, and playbooks. These are operational behavior, not just deployment configuration; review changes with Ahmed.
- `docs/API_CONTRACT.md` describes proposed `/api/v1` paths not found in the current API. Reconcile documentation with the implementation.
- The current API has no general telemetry ingestion endpoint or external vendor-device-to-Core-asset mapping interface.
- `/health` returns `{"status":"ok"}`; database readiness behavior was not verified.
- Some current responses use internal database identifiers such as `asset_id`. The future connector contract must not require or expose Core database IDs. Changes to operator-facing APIs require compatibility review.

These findings describe the reviewed branch. They do not mean the target structure has been implemented.

## 3. Proposed target structure

```text
anzma-ai-insight-core/
  app/{api,services,domain,repositories,adapters,config,dashboard}/
  app/domain/{ingestion,assets,historian,operations,alerts,incidents,evidence,ai}/
  app/adapters/{database,model_providers}/
  db/{migrations,seed}/
  tests/{unit,integration,scenarios}/
  deploy/{compose,examples}/
  docs/ scripts/
```

This is a proposal for the Core repository. It does not create Milestone or connector projects during this sprint.

## 4. Minimal-disruption migration sequence

### Step 1 — Capture current behavior

- Record branch, commit, environment, commands, and actual baseline outputs.
- Identify current dashboard, KPI, alert, incident, evidence, assistant, and fallback paths.
- Do not move code before a reproducible check exists for the behavior being moved.

### Step 2 — Establish configuration and database boundaries

- Centralize environment-variable parsing and validation.
- Use shared database configuration and connection lifecycle.
- Move database operations out of route handlers incrementally into repositories.
- Preserve current API behavior while changing internal wiring.
- Do not change canonical schema, KPI, evidence, freshness, or rule semantics without Ahmed's review.

### Step 3 — Separate API routes from application services

- Routes validate inputs, invoke services, and map results to HTTP responses.
- Extract one responsibility at a time behind services.
- Preserve existing endpoint paths and response behavior unless a compatibility plan is approved.
- Retain the current startup entry point until the extracted application is launchable and regression-checked.

### Step 4 — Agree and consolidate schema ownership

- Confirm canonical data model and schema authority with Ahmed.
- Evolve schema through versioned migrations.
- Make deployment and test setup use the same migration source.
- Separate schema migration from optional seed/demo data where practical.
- Do not remove either schema copy until consumers are identified and replacement usage is verified.

### Step 5 — Isolate AI orchestration

- Route model calls through the provider interface.
- Preserve evidence retrieval and deterministic fallback when the model is disabled or unavailable.
- Keep assistant response behavior and report model/source/fallback status accurately.
- Integrated Ollama/Qwen success remains unverified until a real API request succeeds.

### Step 6 — Keep dashboard behind Core APIs

- Dashboard obtains operational data through Core APIs and does not own database connections or operational calculations.
- Preserve pages and KPI behavior as data access moves behind services.
- Verify filters, time ranges, freshness, and state labels remain consistent between API and dashboard.

### Step 7 — Investigate legacy and duplicated code

- Trace imports and runtime registration for the chat path before deciding its disposition.
- Identify all consumers of both schema copies before consolidation.
- Use dependency and runtime evidence to classify modules as active, legacy, duplicated, or unused.
- Deletion is a separate reviewed change; this plan does not authorize deletion.

## 5. IoT boundary during restructuring

Current `POST /events` is not a general telemetry-reading interface. It accepts an internal `asset_id`; it does not provide connector-safe mapping for sensor observations.

The target Core should accept normalized observations from a future connector using source and stable external device identity. Core maps that identity internally, stores observation history, derives operational state, and presents latest values with explicit timestamp, freshness, and quality. Connectors do not write directly to Core tables or depend on database IDs. Real building connectivity and vendor connector implementation remain outside this sprint.

Ahmed must review the observation contract, mapping model, duplicate policy, and freshness rules before implementation.

## 6. Compatibility controls

For each extraction:

1. Identify old and proposed new paths.
2. Record current inputs, outputs, and operational behavior.
3. Move one responsibility behind a boundary.
4. Run the same reproducible scenario against the new path.
5. Compare response shape and operational results.
6. Retain the old path until the replacement passes its regression check.
7. Document and review any intentional behavior change.

## 7. Plugin boundary

Core may contain generic versioned integration contracts and ingestion validation. It must not contain Milestone SDKs, vendor-specific polling/credentials/protocol behavior, or connector packages in the Core runtime. Connectors use Core interfaces and do not write Core tables or import Core domain internals.

## 8. Out of scope this sprint

- Building the separate Milestone project or vendor connector projects.
- Connecting a real building/customer system.
- Equipment control or autonomous workflow execution.
- Model training or adding providers.
- Rewriting the application wholesale.
- Deleting legacy code without dependency and runtime evidence.

## 9. Completion evidence

A restructuring step is complete only when the old/new paths and ownership are documented, a reproducible check covers affected behavior, existing behavior is preserved or an intentional change is approved, relevant tests have actual recorded output, and Ahmed reviews changes affecting canonical data, KPIs, evidence, freshness, or rules.
