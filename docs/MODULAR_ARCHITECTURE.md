# Anzma AI Insight Core — Modular Architecture

**Status:** Proposed architecture for Week 3 productization
**Scope:** Core only. Milestone XProtect and other vendor connectors remain separate projects.

## 1. Purpose

Anzma AI Insight Core is the reusable operational-intelligence platform for smart buildings. It receives normalized operational data through defined interfaces, stores its history, derives operational state and KPIs, manages alerts and incidents, and presents evidence-based information through its dashboard and assistant.

The Core is an IoT operational platform, not merely a database wrapper. It owns the common data model and operational behavior. Vendor SDKs, protocols, polling logic, and vendor credentials belong in separately installable connector projects.

## 2. Architecture principles

1. **Evidence is authoritative.** Stored observations and validated deterministic logic are the source of truth. AI may explain evidence but must not invent readings, faults, or actions.
2. **Connectors normalize; Core interprets.** A connector translates vendor data and identity into the Core contract. Core owns common asset mapping, operational state, KPIs, and alert behavior.
3. **External identity is mapped inside Core.** Connectors identify a device using a stable source and external device identifier. Core resolves that identity to its own asset. Connector contracts must not require Core database IDs.
4. **Observations, derived state, and incidents are distinct.** A reading is evidence. Current state and KPIs are derived from evidence. Alerts and incidents represent operational conditions and their lifecycle.
5. **Missing or stale data is explicit.** Missing, invalid, or stale observations must not be displayed as healthy.
6. **Recommendations are read-only.** Deterministic rules may combine related observations into recommendations. Equipment control is outside this sprint.
7. **Deployment configuration is external to source code.** Customer environments are configured through environment variables, secrets, and deployment settings rather than customer-specific code edits.
8. **AI providers are replaceable.** Orchestration depends on a provider interface rather than a specific model vendor.
9. **Preserve existing behavior incrementally.** Restructure behind boundaries in small steps and retain existing behavior until regression checks demonstrate parity.

## 3. Proposed Core modules

| Module | Responsibility | Owns | Must not own |
|---|---|---|---|
| API and application services | Validate requests, invoke application services, and format HTTP or real-time responses | API contracts, error mapping, application use cases | SQL queries embedded in routes, vendor protocol logic |
| Ingestion and identity mapping | Accept normalized observations/events; validate identity, timestamps, units, and quality; map external identity to Core assets; apply agreed duplicate/late-data behavior | Core ingestion contract and mapping behavior | Vendor SDKs, polling, vendor-specific alert rules |
| Historian and persistence | Store observations and operational records and retrieve them through repositories | Database adapters, repositories, migrations | Dashboard-specific SQL, AI-generated facts |
| Operational state and KPI services | Derive latest state, freshness, availability, and KPIs from stored evidence | Deterministic calculations and data-sufficiency rules | Vendor-specific interpretation, unsupported healthy status |
| Alert and incident services | Evaluate approved deterministic rules, create/update alerts and incidents, link evidence, and track lifecycle state | Alert and incident rules and transitions | Device control, vendor integration code |
| Evidence and validation services | Retrieve supporting records and check whether claims are supported | Evidence references, grounding, sufficiency, limitations | Creating readings or claiming validation without checks |
| AI orchestration and model providers | Route assistant requests, retrieve evidence, invoke configured providers, and use deterministic fallback | Provider interface, orchestration flow, model/tool metadata | Unrestricted database access, authoritative operational decisions |
| Dashboard and user interface | Present KPIs, latest operational state, freshness, alerts, incidents, evidence, and assistant responses through Core APIs | Presentation and user interaction | Business rules, direct database connections |
| Future workflow execution | Define a boundary for approved deterministic workflows if authorized later | Future workflow interface | Unapproved autonomous actions or equipment control |
| Integration contracts | Define versioned interfaces for separately installed connectors and external applications | Contract versioning and compatibility rules | Vendor SDKs or vendor-specific runtime code |

## 4. IoT data and dashboard flow

```text
Vendor system or device
        -> Separate connector (read, translate, normalize)
        -> Core ingestion interface (validate, map identity, deduplicate)
        -> Historian (persist observations and timestamps)
        -> Operational services (derive state, freshness, KPIs, alerts)
        -> Dashboard/API and evidence-grounded assistant
```

The dashboard should display the latest stored value with its unit, sensor-observed timestamp, and freshness or quality. It should distinguish:

- **Current:** a valid observation exists within the configured freshness window.
- **Stale:** an observation exists but is older than that window.
- **Missing:** no observation exists for the requested signal.
- **Invalid/poor quality:** an observation exists but cannot be treated as reliable.
- **Recovered:** a previously active condition has cleared, with recovery time retained.
- **Historical:** an earlier observation or resolved incident, not current state.

Freshness thresholds and quality semantics must be agreed with Ahmed against the canonical data model. Absence of an alert alone must not be shown as healthy.

## 5. Ingestion identity and normalized observation contract

The reviewed implementation does not provide a general telemetry ingestion API or vendor-device identity mapping interface. These are proposed Core capabilities, not existing behavior.

A future normalized observation should include source/connector identity, stable external device identity, metric, value, unit, sensor-observed time with timezone, Core-received time, quality/validity where available, and an idempotency or duplicate-handling mechanism.

Example:

```json
{
  "source": "site_gateway_1",
  "external_device_id": "ahu-03/supply-temp",
  "metric": "supply_air_temperature",
  "value": 18.6,
  "unit": "°C",
  "observed_at": "2026-11-15T09:30:00Z"
}
```

Core resolves `(source, external_device_id)` to its internal asset and metric. A connector does not submit a PostgreSQL `asset_id` and does not write directly to Core tables. Unknown mappings, invalid units, duplicates, out-of-order readings, and poor-quality data need explicit policies and outcomes before implementation.

## 6. Example: evidence-based recommendation

If approved sources provide room temperature, window state, and occupancy:

1. A connector reads and normalizes those observations.
2. Core maps each external identity to its Core asset and stores values with observed and received times.
3. A deterministic rule confirms required observations exist and are valid and fresh.
4. Core may produce a read-only recommendation, such as “Review cooling: room temperature is high while the window is open and occupancy is present.”
5. The dashboard displays the recommendation with its supporting observations and limitations.

This does not automatically close the window or control the AC. The current demo lacks the complete signal set, ingestion path, mapping, and approved rule for this example.

## 7. Proposed Core repository shape

```text
anzma-ai-insight-core/
  app/
    api/ services/ domain/ repositories/ adapters/ config/ dashboard/
    domain/{ingestion,assets,historian,operations,alerts,incidents,evidence,ai}/
    adapters/{database,model_providers}/
  db/{migrations,seed}/
  tests/{unit,integration,scenarios}/
  deploy/{compose,examples}/
  docs/ scripts/
```

This is a target proposal, not the current repository structure. Restructuring should be incremental while preserving the current application.

## 8. Plugin and external application boundary

- Core contains generic contracts and adapters only.
- Milestone XProtect and other vendor integrations are separate, independently installable projects.
- Connectors communicate through versioned Core interfaces, not direct database writes or Core database IDs.
- Core domain modules do not import vendor SDKs or connector implementations.
- This sprint defines the boundary; it does not start the separate integration projects.

## 9. Ownership and review

- **Amr — Platform Integration & Agent Orchestration:** API boundaries, application structure, runtime/deployment integration, AI orchestration/provider boundary, and dashboard/API consistency.
- **Ahmed — Data Intelligence & AI Reliability:** canonical data model, evidence/KPI integrity, freshness/quality semantics, and deterministic operational logic.
- **Joint review:** ingestion contract, external identity mapping, schema changes, and API decisions involving internal identifiers.

## 10. Open decisions

Confirm with Ahmed before implementing related changes:

1. Canonical metric names, units, quality values, and asset mapping model.
2. Freshness thresholds by signal/metric.
3. Duplicate and late-observation policy and connector acknowledgment behavior.
4. Customer/site isolation model.
5. API and real-time authentication/authorization.
6. Operator-facing internal-ID policy and compatibility plan.
7. Readiness behavior when PostgreSQL or Ollama is unavailable.
