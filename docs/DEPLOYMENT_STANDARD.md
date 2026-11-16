# Anzma AI Insight Core — Deployment Standard

**Status:** Proposed deployment standard; current deployment behavior is not fully verified
**Scope:** Repeatable Core installation without customer-specific source-code changes

## 1. Deployment goals

- Install the same Core application in different customer environments.
- Supply environment-specific values externally.
- Keep secrets out of source, images, and logs.
- Initialize and upgrade the database through controlled migrations.
- Preserve database data across container restart/replacement.
- Distinguish process liveness from dependency readiness.
- Keep evidence retrieval and deterministic behavior available when the model is unavailable.
- Provide installation, upgrade, backup, and troubleshooting instructions.

## 2. Proposed deployment components

```text
Docker Compose
  - Core API and dashboard
  - PostgreSQL with persistent storage
  - Optional Ollama service or externally managed local Ollama endpoint
```

This is a proposal, not verified deployment behavior. Docker execution was unavailable during the baseline review.

## 3. Configuration contract

The reviewed application reads `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`, `CHAT_USE_LLM`, `DETECT_INTERVAL`, `ALERT_POLL_INTERVAL`, `OLLAMA_HOST`, `OLLAMA_MODEL`, `OLLAMA_TIMEOUT`, and `ANZMA_DRIVER`.

| Group | Settings | Requirements |
|---|---|---|
| Database | `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD` | Password supplied as a secret; host/port are deployment-specific |
| AI provider | `CHAT_USE_LLM`, `OLLAMA_HOST`, `OLLAMA_MODEL`, `OLLAMA_TIMEOUT` | Model may be disabled; outage must not disable deterministic fallback |
| Runtime | `DETECT_INTERVAL`, `ALERT_POLL_INTERVAL`, log level | Validate values and bounds at startup |
| API security | Authentication/authorization settings when selected | Decide before customer deployment; do not assume unauthenticated access is production-ready |

Defaults differ across reviewed modules, and Compose does not pass every runtime setting consistently. The implementation must establish one settings source, consistent defaults, and explicit validation.

## 4. Secrets handling

- Keep real `.env` files out of version control; provide placeholders only in `.env.example`.
- Never put credentials in Dockerfiles, source, images, logs, or example commands.
- Use a protected local environment file for development and the deployment platform's secret mechanism for customer installs.
- Rotate credentials through deployment configuration, not source edits.
- Do not expose PostgreSQL or Ollama ports outside the intended network without an explicit deployment decision.

## 5. Database initialization and migrations

- Confirm the canonical schema/migration authority with Ahmed.
- Use versioned migrations and record applied versions.
- Make migrations repeatable and fail clearly when they cannot complete.
- Separate schema migration from optional seed/demo data where practical.
- Back up persistent data before upgrades.
- Use one canonical migration source for deployment and tests.
- Verify both clean installation and supported upgrade paths.

The current working tree has one bootstrap schema at `db/init/anzma_schema_and_seed.sql`; Compose and test bootstraps use versioned migrations from `db/migrations/`. See `DATABASE_BASELINE.md` for the sequence. Do not restore or add a second runtime schema copy.

## 6. Startup, liveness, and readiness

- Validate required configuration at startup; errors must not reveal secret values.
- Liveness reports whether the process runs. Readiness reports whether dependencies required for advertised behavior are available.
- Current `/health` returns `{"status":"ok"}`; database readiness behavior was not verified.
- Compose startup order alone does not prove PostgreSQL readiness. Use a readiness check and bounded retry.
- If Ollama is optional, report model unavailability separately and preserve deterministic fallback.
- API process health does not prove sensor data is current or equipment is healthy.

## 7. Persistent storage and restart behavior

- Store PostgreSQL data in a named persistent volume or managed persistent location.
- Replacing/restarting the application container must not erase operational history.
- Keep application containers stateless unless a documented feature requires local state.
- Decide whether included Ollama model files use persistent storage.
- Document and verify backup/restore before commercial release.
- Define log storage and retention expectations.

## 8. Local AI model configuration

- Select models through configuration and the provider interface.
- Document the model name and provisioning procedure.
- Make model download an explicit setup step if not already installed.
- Model timeout/outage must not block evidence retrieval or deterministic fallback.
- Integrated Ollama/Qwen success remains outstanding until a request through the application succeeds.
- Measure performance on target deployment hardware; do not infer it from the development laptop.

## 9. Proposed installation sequence

Exact commands must be added after implementation and execution in the target environment.

1. Confirm supported host prerequisites.
2. Obtain the approved Core artifact and deployment files.
3. Create protected configuration from the safe example.
4. Set database, runtime, security, and optional Ollama settings.
5. Start PostgreSQL and confirm readiness.
6. Apply migrations and optional seed data for the selected install mode.
7. Start Core API and dashboard.
8. Verify liveness and readiness.
9. Open the dashboard and run documented smoke scenarios.
10. Record release, migration, configuration profile (without secrets), and actual test results.

## 10. Troubleshooting guide

| Symptom | Checks |
|---|---|
| Core fails during startup | Configuration validation and required settings; do not print secrets |
| Database connection fails | PostgreSQL status, service hostname, port, credentials, readiness |
| Dashboard has no operational data | API responses, DB contents, filters/time range, data availability |
| Readings are stale or absent | Source path, timestamps, identity mapping, and quality state |
| Assistant uses fallback | `CHAT_USE_LLM`, Ollama reachability, model name, timeout, model availability |
| Alerts do not appear | Input data, detector execution, configured rules, logs |
| Data disappears after restart | Persistent volume and backup/restore configuration |

## 11. Release verification gates

Do not mark deployment verified until actual results show: clean installation; configuration without source edits; safe secrets handling; fresh and upgrade migrations; data persistence across restart; clear invalid-configuration failure; distinct liveness/readiness behavior; accurate database-unavailable behavior; integrated Ollama/Qwen success; documented model outage fallback; and useful troubleshooting without secret exposure.
