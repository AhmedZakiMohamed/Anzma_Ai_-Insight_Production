# Anzma AI Insight Core — API Contract V1

**Status:** Current API inventory for the reviewed `final` branch
**Reviewed commit:** `87e084d753bcc478b20898b9b9507a2013955ceb`
**Important:** This is an implementation inventory, not a claim that the future telemetry interface exists.

## 1. Scope and conventions

- Current FastAPI routes are defined in `App.py` and do not consistently use an `/api/v1` prefix.
- The route details below summarize reviewed code. Capture the running application's OpenAPI schema and route outputs before treating this as a machine-verified contract.
- Some current responses expose internal identifiers such as `asset_id`. The future connector contract must not require or expose Core database IDs. Changes to operator-facing responses require compatibility review.
- Production authentication/authorization behavior was not verified.

## 2. Implemented HTTP routes

| Method | Path | Purpose | Notes |
|---|---|---|---|
| GET | `/` | Serve landing page | HTML |
| GET | `/dashboard` | Serve dashboard | HTML |
| GET | `/health` | Process liveness | `{"status":"ok"}`; readiness not verified |
| GET | `/dashboard/filters` | Dashboard filter options | Implemented |
| GET | `/dashboard/summary` | Summary KPIs | System/building/time filters; UTC `Z` timestamps; maximum period 31 days |
| GET | `/dashboard/overview` | Operational overview | `hours` default 24, maximum 168 |
| GET | `/dashboard/trends` | Trend data | `hours` default 24, maximum 168; optional system type |
| GET | `/dashboard/equipment-health` | Equipment health data | `hours` default 24, maximum 168; may expose internal asset IDs |
| GET | `/alerts` | List alerts | Capture exact schema from running API |
| GET | `/alerts/{alert_id}` | Retrieve alert | Capture exact schema from running API |
| POST | `/alerts` | Create manual alert | See section 4 |
| POST | `/alerts/run` | Run alert detection | `hours` optional, range 1–720 |
| POST | `/alerts/{alert_id}/explain` | Explain alert | `use_llm=false` supports non-LLM path |
| GET | `/incidents` | List incidents | Implemented |
| GET | `/incidents/{incident_id}` | Retrieve incident | Implemented |
| POST | `/incidents/{incident_id}/status` | Update incident status | Implemented |
| GET | `/incidents/{incident_id}/evidence` | Retrieve incident evidence | Implemented |
| GET | `/chat/questions` | Example supported questions | Implemented |
| POST | `/ask` | Ask narrow HVAC/CRAC question | Bounded domain |
| POST | `/chat` | Ask assistant | See section 5 |
| POST | `/events` | Submit one operational event | Not a general sensor telemetry API; see section 3 |

Socket.IO event `new_alert` is implemented for alert notifications. Authorization and delivery guarantees were not verified.

## 3. `POST /events`

Current request fields:

- `asset_id`: integer Core database asset identifier.
- `event_type`: event type.
- `ts`: event timestamp.
- `severity`: optional; defaults to `INFO`.
- `details`: optional details.

Reviewed behavior includes asset/event/system compatibility validation, five-minute future-time tolerance, and a uniqueness constraint. Duplicate submission returns HTTP 409.

This submits a single operational event. It is not a continuous sensor-reading contract and is unsuitable as the future connector interface because it requires an internal ID.

## 4. `POST /alerts`

Current request fields include `rule_id`, `asset_id`, `severity`, `summary`, and `first_ts`; optional fields include `title`, `last_ts`, and `facts`.

Observed outcomes include HTTP 201 for creation, HTTP 409 for duplicate, and HTTP 422 for invalid rule or asset. Capture exact request/response examples from the running application before treating this summary as complete.

This is an operational alert API, not a vendor connector interface.

## 5. `POST /chat`

The endpoint accepts a message and optional time-window, model, history, and session-state fields. Response fields include `answer`, `language`, `source`, `grounding.passed`, `sources`, `data_sufficient`, and `window`; additional metadata may be present.

The assistant should explain available evidence. Successful Ollama/Qwen execution through the integrated application remains unverified and is a Sprint 1 acceptance item.

## 6. Future connector ingestion interface — proposed, not implemented

A versioned interface for normalized IoT observations must define:

- Source/connector identifier and stable external device identifier.
- Metric, value, and unit.
- Sensor-observed timestamp and Core-received timestamp.
- Quality/validity fields where available.
- Duplicate/idempotency and late/out-of-order behavior.
- Unknown mapping and validation errors.
- Authentication and customer/site authorization.

Core maps `(source, external_device_id)` to its internal asset. Connectors must not know database IDs or write Core tables. Exact path, payload, batching, response codes, authentication, and retry behavior remain open. Any `/api/v1/...` ingestion path in existing docs is proposed until implemented and verified.

## 7. Documentation discrepancy

The reviewed `docs/API_CONTRACT.md` describes proposed `/api/v1` routes that were not found in the current API. Update it or clearly label those routes as proposals; keep this implemented-route inventory aligned with runtime OpenAPI output.

## 8. Verification required before release

1. Run the application and capture its OpenAPI schema.
2. Compare paths, parameters, request/response fields, and errors with route tests and actual outputs.
3. Add synthetic request/response examples.
4. Decide how operator-facing APIs handle internal database identifiers.
5. Document authentication, authorization, and versioning.
6. Keep the connector interface marked unimplemented until implemented and tested.
