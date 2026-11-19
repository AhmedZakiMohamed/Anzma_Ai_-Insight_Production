# INGESTION_CONTRACT.md — Anzma AI Insight Core

**Contract version:** `1.0`
**Status:** v1 ingestion decisions approved and implemented in the Core. Existing write path `POST /events` (one event, internal `asset_id`, no provenance) remains supported for compatibility.
**Audience:** connector developers (any vendor, any language) and Core developers.
**Companion documents:** [`CONNECTOR_ARCHITECTURE.md`](CONNECTOR_ARCHITECTURE.md) (who does what), [`CONTRACT_TO_DB_MAPPING.md`](CONTRACT_TO_DB_MAPPING.md) (how it lands in PostgreSQL), [`CANONICAL_DATA_MODEL.md`](CANONICAL_DATA_MODEL.md) (entity semantics), [`HISTORIAN_DESIGN.md`](HISTORIAN_DESIGN.md) (storage, retention, buffering).

**How to read this document.** Entity semantics (assets, metrics, quality meanings, freshness maths, state model) are defined once in `CANONICAL_DATA_MODEL.md` and are referenced, not repeated. This document owns the **wire format**: envelope, record schemas, validation order, rejection codes, response shapes, authentication. Where an approved decision changes the canonical model, it is listed in §14 so the canonical document can be amended.

Tags: **[Decided]** approved by the team; **[Proposed]** reserved for future work and not part of v1.

---

## 1. Principles

1. **Connectors convert, the Core validates.** Vendor names, units and time zones are normalised by the connector. The Core never contains vendor-specific code and never branches on manufacturer.
2. **One door.** Every external source writes through the same ingestion interface. Seed scripts and simulators will eventually use it too.
3. **Evidence is never invented.** The Core stores what was sent, stamps its own receive time, and records what it refused and why. It does not infer `OFFLINE` from silence, and it does not repair bad data silently.
4. **Idempotent by construction.** A connector may resend any batch any number of times with the same identifiers and the Core state does not change.
5. **Core internal IDs never cross the wire.** Connectors use `asset_code` or the vendor's own `external_id`, never `asset_id`.

---

## 2. Transport and conventions

| Item | Rule |
|---|---|
| Protocol | HTTPS, JSON (`Content-Type: application/json; charset=utf-8`). TLS is mandatory outside local development. |
| Base path | `/api/v1/ingest`; approved versioned Core route. |
| Authentication | `Authorization: Bearer <api_key>` (see §11). Authentication is transport-level and **not** part of the payload, so the method can change later without a new contract version. |
| Field names | `snake_case`. |
| Codes | `metric_code` and `event_type` are lowercase `snake_case` and equal `metric_types.name` / `event_types.name`. `asset_code`, `building_code`, `source_code` match `^[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}$`. |
| Enumerations | `UPPERCASE` in the contract (`GOOD`, `CRITICAL`, …). The Core maps them to its stored form (existing tables store severity in lowercase). |
| Numbers | JSON numbers, finite. No strings, no `NaN`/`Infinity`. |
| Strings | UTF-8. `external_id` ≤ 128 chars. `message` ≤ 500 chars. |
| Opaque objects | `details` and `source_metadata` ≤ 8 KiB serialized each. |
| Unknown fields | Ignored; the response lists them in `warnings`. Unknown **enum values** and unknown `record_type` are rejected. |
| Body size | ≤ 5 MiB; batches also have a 5000-record maximum. |

### Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/v1/ingest/batches` | Submit a batch of telemetry / event / state records. A single record is a batch of one. |
| `GET` | `/api/v1/ingest/batches/{batch_id}` | Retrieve the stored result of a batch previously sent by the authenticated source (full rejection list). |

One endpoint carries all three record types (`record_type` discriminates) so there is one validation pipeline and one idempotency mechanism. Per-type convenience endpoints are not in v1. Permission to send a type is enforced per record through key scopes (§11).

---

## 3. Time semantics

| Name | Meaning | Who sets it | Where it lives |
|---|---|---|---|
| `observed_at` | When the measurement was taken, **or when the event/state change occurred**, at the source. | Connector | `telemetry_records.ts`, `event_records.ts` |
| `received_at` | When the Core accepted the record. Core clock. **Never accepted from the sender.** | Core | new column |
| `sent_at` | When the connector sent the batch (envelope). Diagnostic only. Used to estimate clock skew; never stored as a fact time. | Connector | `ingestion_batches` |
| `evaluated_at` | When the Core computed a derived value such as freshness or state. | Core | state tables (later) |

**On `event_time`.** There is no separate `event_time` field in v1. For event records, `observed_at` **is** the event time (the moment the event happened at the source). If a vendor also provides a separate "logged at" time, the connector may put it in `source_metadata`.

### Rules

| Topic | Rule |
|---|---|
| Format | RFC 3339 in **UTC with a trailing `Z`**, fractional seconds allowed (≤ 6 digits). Example `2026-11-15T09:15:00Z`. |
| Time zones | Connectors convert to UTC before sending. A timestamp with any offset (`+02:00`) or without `Z` is **rejected** (`INVALID_TIMESTAMP`), not silently converted. The original local time may be kept in `source_metadata.original_time`. Display time zones are a Core/UI concern (building time zone). |
| Future data | `observed_at` must be ≤ `received_at + 5 min` (same tolerance `POST /events` uses today). Otherwise `FUTURE_TIMESTAMP`. |
| Too old | No age cutoff is enforced by v1; valid late/backfill records are accepted subject to natural-key conflict rules. |
| Late-arriving data | **Accepted**, never rejected for lateness alone. A record is *late* when `received_at − observed_at` exceeds the source's delay threshold (default 15 min **[Proposed]**). Lateness is derived, not stored. Late data never rewrites existing samples. Detection runs are idempotent, so late records are evaluated on the next run. |
| Ordering | Records may arrive in any order. The Core orders by `observed_at`. |
| Precision | Retries must reproduce `observed_at` **exactly**, including fractional seconds, or the retry is a different record. |

---

## 4. Batch envelope

Every request is one envelope. Envelope problems reject the **whole** batch; record problems reject only that record (§8).

JSON Schema (draft 2020-12):

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://anzma.example/schemas/ingestion-batch-1.0.json",
  "title": "Anzma ingestion batch, contract 1.0",
  "type": "object",
  "required": [
    "contract_version",
    "batch_id",
    "sent_at",
    "source",
    "building_code",
    "records"
  ],
  "properties": {
    "contract_version": {
      "type": "string",
      "pattern": "^[0-9]+\\.[0-9]+$"
    },
    "batch_id": {
      "type": "string",
      "pattern": "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
    },
    "sent_at": {
      "type": "string",
      "description": "Sender clock, UTC Z. Informational (clock-skew diagnostics); never used as observed_at."
    },
    "delivery_mode": {
      "enum": [
        "LIVE",
        "BACKFILL"
      ],
      "default": "LIVE"
    },
    "source": {
      "type": "object",
      "required": [
        "source_code",
        "connector"
      ],
      "properties": {
        "source_code": {
          "type": "string",
          "pattern": "^[A-Za-z0-9][A-Za-z0-9_.\\-]{0,63}$"
        },
        "connector": {
          "type": "object",
          "required": [
            "name",
            "version"
          ],
          "properties": {
            "name": {
              "type": "string",
              "maxLength": 64
            },
            "version": {
              "type": "string",
              "maxLength": 32
            }
          }
        },
        "connector_status": {
          "type": "object",
          "description": "Optional diagnostics.",
          "properties": {
            "vendor_link": {
              "enum": [
                "CONNECTED",
                "DISCONNECTED",
                "UNKNOWN"
              ]
            },
            "buffered_records": {
              "type": "integer",
              "minimum": 0
            },
            "dropped_records": {
              "type": "integer",
              "minimum": 0
            },
            "last_poll_at": {
              "type": "string"
            }
          }
        }
      }
    },
    "building_code": {
      "type": "string",
      "pattern": "^[A-Za-z0-9][A-Za-z0-9_.\\-]{0,63}$"
    },
    "records": {
      "type": "array",
      "minItems": 0,
      "maxItems": 5000,
      "items": {
        "oneOf": [
          {
            "type": "object",
            "required": [
              "record_type",
              "asset",
              "metric_code",
              "value",
              "unit",
              "observed_at"
            ],
            "properties": {
              "record_type": {
                "const": "telemetry"
              },
              "asset": {
                "type": "object",
                "description": "Exactly one of asset_code (Anzma code) or external_id (vendor ID resolved through the sending source).",
                "properties": {
                  "asset_code": {
                    "type": "string",
                    "pattern": "^[A-Za-z0-9][A-Za-z0-9_.\\-]{0,63}$"
                  },
                  "external_id": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 128
                  }
                },
                "oneOf": [
                  {
                    "required": [
                      "asset_code"
                    ]
                  },
                  {
                    "required": [
                      "external_id"
                    ]
                  }
                ]
              },
              "metric_code": {
                "type": "string",
                "pattern": "^[a-z][a-z0-9_]{0,62}$"
              },
              "value": {
                "type": "number"
              },
              "unit": {
                "type": "string",
                "minLength": 1,
                "maxLength": 16
              },
              "observed_at": {
                "type": "string",
                "description": "RFC 3339 UTC, must end with Z (checked by rule INVALID_TIMESTAMP)."
              },
              "quality": {
                "enum": [
                  "GOOD",
                  "UNCERTAIN",
                  "BAD"
                ],
                "default": "GOOD"
              },
              "source_metadata": {
                "type": "object",
                "description": "Opaque JSON, max 8 KiB serialized."
              }
            }
          },
          {
            "type": "object",
            "required": [
              "record_type",
              "source_event_id",
              "asset",
              "event_type",
              "observed_at"
            ],
            "properties": {
              "record_type": {
                "const": "event"
              },
              "source_event_id": {
                "type": "string",
                "minLength": 1,
                "maxLength": 128
              },
              "asset": {
                "type": "object",
                "description": "Exactly one of asset_code (Anzma code) or external_id (vendor ID resolved through the sending source).",
                "properties": {
                  "asset_code": {
                    "type": "string",
                    "pattern": "^[A-Za-z0-9][A-Za-z0-9_.\\-]{0,63}$"
                  },
                  "external_id": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 128
                  }
                },
                "oneOf": [
                  {
                    "required": [
                      "asset_code"
                    ]
                  },
                  {
                    "required": [
                      "external_id"
                    ]
                  }
                ]
              },
              "event_type": {
                "type": "string",
                "pattern": "^[a-z][a-z0-9_]{0,62}$"
              },
              "observed_at": {
                "type": "string",
                "description": "Time the event occurred at the source (the 'event time')."
              },
              "severity": {
                "enum": [
                  "INFO",
                  "WARNING",
                  "CRITICAL"
                ],
                "default": "INFO"
              },
              "details": {
                "type": "object",
                "description": "Domain details; existing consumers read keys such as reason, badge, port."
              },
              "source_metadata": {
                "type": "object",
                "description": "Opaque JSON, max 8 KiB serialized."
              }
            }
          },
          {
            "type": "object",
            "required": [
              "record_type",
              "asset",
              "observed_at"
            ],
            "anyOf": [
              {
                "required": [
                  "reported_state"
                ]
              },
              {
                "required": [
                  "availability"
                ]
              }
            ],
            "properties": {
              "record_type": {
                "const": "state"
              },
              "asset": {
                "type": "object",
                "description": "Exactly one of asset_code (Anzma code) or external_id (vendor ID resolved through the sending source).",
                "properties": {
                  "asset_code": {
                    "type": "string",
                    "pattern": "^[A-Za-z0-9][A-Za-z0-9_.\\-]{0,63}$"
                  },
                  "external_id": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 128
                  }
                },
                "oneOf": [
                  {
                    "required": [
                      "asset_code"
                    ]
                  },
                  {
                    "required": [
                      "external_id"
                    ]
                  }
                ]
              },
              "observed_at": {
                "type": "string"
              },
              "reported_state": {
                "enum": [
                  "RUNNING",
                  "STOPPED",
                  "STANDBY",
                  "FAULT",
                  "UNKNOWN"
                ]
              },
              "availability": {
                "enum": [
                  "AVAILABLE",
                  "UNAVAILABLE",
                  "UNKNOWN"
                ]
              },
              "reason_code": {
                "type": "string",
                "pattern": "^[A-Z][A-Z0-9_]{0,63}$"
              },
              "message": {
                "type": "string",
                "maxLength": 500
              },
              "source_metadata": {
                "type": "object",
                "description": "Opaque JSON, max 8 KiB serialized."
              }
            }
          }
        ]
      }
    }
  }
}
```

| Field | Required | Notes |
|---|---|---|
| `contract_version` | yes | `MAJOR.MINOR`. Core accepts the current MAJOR; older MAJORs only during a published deprecation window **[Open]**. |
| `batch_id` | yes | UUID chosen by the connector. Idempotency key together with the authenticated source. |
| `sent_at` | yes | Sender clock, UTC `Z`. |
| `delivery_mode` | no | `LIVE` (default) or `BACKFILL` (store-and-forward replay after an outage). Informational: it never makes an asset look fresh (§9). |
| `source.source_code` | yes | Must equal the source bound to the API key, else `403 SOURCE_MISMATCH`. |
| `source.connector` | yes | Name and version, for diagnostics and audit. |
| `source.connector_status` | no | Optional diagnostics: `vendor_link`, `buffered_records`, `dropped_records`, `last_poll_at`. Lets the Core tell "the connector lost its vendor system" from "one device went quiet". **[Proposed]** |
| `building_code` | yes | One batch serves one building. A connector serving two buildings registers two sources (per `CANONICAL_DATA_MODEL.md` §12). |
| `records` | yes | `0..5000` items. An empty array is a **keepalive**: it updates source liveness and nothing else **[Proposed]**. |

> **Implementation note.** The schema above nests all three record schemas under `records.items` for readability. The Core validates the envelope with `records` as opaque objects, then validates **each record on its own** by `record_type`, so one bad record produces one rejection instead of failing the batch.

---

## 5. Record types

### 5.1 Telemetry

Sensor readings: temperature, pressure, humidity, power, packet loss, and so on.

| Field | Req. | Type | Rule |
|---|---|---|---|
| `record_type` | yes | `"telemetry"` | |
| `asset` | yes | object | Exactly one of `asset_code` or `external_id`. |
| `metric_code` | yes | string | Must exist in `metric_types` and be allowed for the asset's system (`metric_type_system_compat`). |
| `value` | yes | number | Finite. |
| `unit` | yes | string | Must **equal** the metric's canonical unit (`metric_types.unit`). **[Proposed]** v1 has no Core-side unit conversion: the connector converts, a wrong unit is `UNIT_MISMATCH`. |
| `observed_at` | yes | string | §3. |
| `quality` | no | enum | `GOOD` (default), `UNCERTAIN`, `BAD`. See §7. |
| `source_metadata` | no | object | Raw value/unit/reference for audit. |

Natural key: `(asset, metric_code, observed_at)`.

### 5.2 Event

Discrete happenings: door forced, camera offline, link down, alarm, heartbeat.

| Field | Req. | Type | Rule |
|---|---|---|---|
| `record_type` | yes | `"event"` | |
| `source_event_id` | yes | string | The vendor's own event ID, or a deterministic value the connector derives (for example a hash of source, asset, type and `observed_at`). **[Implemented: required]** It is what keeps two distinct events in the same second from collapsing into one. |
| `asset` | yes | object | As above. |
| `event_type` | yes | string | Must exist in `event_types` and be allowed for the asset's system (`event_type_system_compat`). A `door_forced` event for a camera is rejected. |
| `observed_at` | yes | string | Event time, §3. |
| `severity` | no | enum | `INFO` (default), `WARNING`, `CRITICAL`. Reported severity; the Core does not rewrite it. |
| `details` | no | object | Domain details. |
| `source_metadata` | no | object | |

Natural key: `(source, source_event_id)`.
Heartbeats are ordinary events of type `heartbeat` (valid for every system today).

### 5.3 Equipment state

What the vendor system **itself knows** about the equipment right now.

| Field | Req. | Type | Rule |
|---|---|---|---|
| `record_type` | yes | `"state"` | |
| `asset` | yes | object | |
| `observed_at` | yes | string | When the state was true at the source. |
| `reported_state` | one of | enum | `RUNNING`, `STOPPED`, `STANDBY`, `FAULT`, `UNKNOWN`. The vendor's run state. |
| `availability` | one of | enum | `AVAILABLE`, `UNAVAILABLE`, `UNKNOWN`. Explicit evidence only. |
| `reason_code` | no | string | `UPPER_SNAKE`, vendor-neutral where possible (`VIDEO_SIGNAL_LOST`). |
| `message` | no | string | ≤ 500 chars. |
| `source_metadata` | no | object | Raw vendor state. |

At least one of `reported_state` / `availability` is required. Natural key: `(source, asset, observed_at)`.

**What a connector must not send** **[Decided, D2]**: `communication_status`, `freshness`, and `operational_state`. These describe the data path and the Core's health assessment, and are computed by the Core (`CANONICAL_DATA_MODEL.md` §8–9). A connector reports only what it knows. A state report never overrides Core-derived health; it is evidence the Core's evaluator may use. **Silence alone never produces `OFFLINE`/`UNAVAILABLE`.**

---

## 6. Identity and mapping

Full rules in `CONNECTOR_ARCHITECTURE.md` §7. Contract-level rules:

| Id | Scope | Meaning |
|---|---|---|
| `source_code` | batch | Registered data source (`data_sources`). Bound to the API key. |
| `building_code` | batch | Registered building. Must match the source's building. |
| `asset.external_id` | record | Vendor's device/point identifier, unique **within the source**. |
| `asset.asset_code` | record | Anzma's stable asset code. |
| `asset_id` | — | Core-internal. **Never** appears in the contract. |

Resolution: `(source, external_id)` → asset through `asset_external_ids`. An `asset_code` is accepted only for assets in the source's own building.

**Unmapped devices [Proposed].** A record whose `external_id` has no mapping is **rejected** with `ASSET_UNRESOLVED`. The Core does **not** auto-create assets. The rejected payload is kept in `ingestion_rejections`, and an "unmapped external IDs" view (source, external_id, count, first/last seen) tells an administrator what to map. After mapping, the connector (or a replay tool) resends the records in a **new** batch.

---

## 7. Data quality

Exactly three values **[Decided, D1]**.

| Value | Meaning | Effect |
|---|---|---|
| `GOOD` | Valid and trusted. Default. | Used by KPIs, alerts, charts. |
| `UNCERTAIN` | Valid, but the source flags low confidence (sensor warming up, substituted value). | Included by default; evidence is marked as uncertain. |
| `BAD` | Structurally valid but unusable (source-declared bad, or outside the metric's plausible range as judged by the Core). | Stored for audit; **excluded** from KPIs and alerts. |

**Missing data is not a quality value.** `MISSING` is not accepted. A value that was not obtained is the *absence* of a record, detected by the Core from freshness and gap analysis (§9). A connector that knows a value is unavailable simply sends no record for it, and reports a degraded link through `connector_status` if relevant.

Invalid data (bad schema, unknown asset/metric, wrong unit, non-finite value) is **rejected**, never stored as `BAD`.

---

## 8. Batch processing

### 8.1 Limits

| Limit | Default | |
|---|---|---|
| Records per batch | `0..5000` | `0` = keepalive. More → `413 BATCH_TOO_LARGE`. |
| Body size | 5 MiB | |
| `details` / `source_metadata` | 8 KiB each | Larger → record rejected `PAYLOAD_TOO_LARGE`. |

v1 enforces a 5 MiB request limit and 5000 records per batch. Connectors should target ≈ 500–1000 records per batch.

### 8.2 Acceptance model — partial acceptance **[Approved for v1, 18 November 2026]**

| Problem found | Result |
|---|---|
| **Envelope-level** (malformed JSON, bad `batch_id`, unknown source/building, `source_code` ≠ authenticated source, unsupported version, too large) | The **whole batch is refused**. HTTP 4xx. Nothing is stored. |
| **Record-level** (any rule in §10) | **Only that record** is rejected, with a reason code. Valid records in the same batch are stored. |

Example: 1000 records, 990 valid, 10 invalid → `accepted: 990`, `rejected: 10`, each rejection has an index and a reason.

Per-record outcomes: `accepted`, `duplicate` (idempotent no-op), `rejected`.

**Atomicity and response meaning.** One database transaction per batch covers accepted fact rows, the `ingestion_batches` row, and `ingestion_rejections`. A transaction/database failure rolls everything back and returns retryable `503`; the client retries the same body and `batch_id`. Record validation failures do not abort the transaction: valid rows and rejected-record audit rows commit together. The response includes `transaction: COMMITTED`; `200 PARTIAL` means mixed outcomes committed. `422 REJECTED` means every record was rejected and that rejection audit committed; clients must not treat it as proof that no server-side operation occurred. Envelope-level errors occur before a batch audit is created.

### 8.3 Batch idempotency

* Key: `(source, batch_id)`.
* Replay with the **same** payload → the stored result is returned unchanged, header `Idempotent-Replay: true`, no side effects.
* Same `batch_id` with a **different** payload (the Core stores a SHA-256 of the body) → `409 BATCH_ID_REUSED`.
* Records rejected in a batch are **not** retried by resending the same `batch_id`. Fix them and send a **new** `batch_id`.

### 8.4 Duplicates and conflicts **[Approved for v1, 18 November 2026]**

| Case | Outcome |
|---|---|
| Same natural key, same content | `duplicate`. Counted, not stored again. |
| Same natural key, different content | **First write wins.** The existing row is untouched; the new record is rejected `CONFLICT` and kept in `ingestion_rejections`. This v1 has no correction/overwrite operation. |

"Same value" for telemetry means `|a − b| ≤ 1e-9 × max(1, |a|)` after the connector's own conversion, so a retried `24.6` versus `24.600000000000001` is a duplicate, not a conflict. For events, `(source, source_event_id)` is the identity and same content compares event type, severity, details, and `observed_at`; reuse with any difference is a conflict. Two different `source_event_id` values may represent distinct events for the same asset/type/instant. Legacy events without source IDs retain the natural key `(asset_id, event_type_id, ts)` and the legacy endpoint still returns 409 for a duplicate. For states, same content compares reported state, availability, reason, and message.

There is **no correction mechanism in v1.** A sender that legitimately needs to correct an earlier value has no supported path yet. **[Open]** `supersedes` semantics for sanctioned corrections, a v1.x candidate.

---

## 9. Freshness

Full formulas live in `CANONICAL_DATA_MODEL.md` §9. This section fixes the contract-visible behaviour.

**Who computes it:** the Core only **[Decided, D2]**. Connectors never send a freshness or communication status.

**Inputs the Core keeps per record:** `last_observed_at` (data age) and `last_received_at` (source liveness).

| Concept | Rule |
|---|---|
| Expected reporting interval `E` | Per `(asset, metric)`; resolved in this order: asset+metric override → asset-type+metric default → source default → global default (telemetry 900 s, heartbeat 3600 s, taken from observed seed behaviour). |
| Stale threshold | `FRESH` if `age ≤ 2E`; `STALE` if `2E < age ≤ 5E`; `MISSING` if `age > 5E`. Factors configurable. |
| `UNKNOWN` | No observation has ever been received for the series. Never shown as `FRESH`. |
| Event-only devices | Door readers and similar have no telemetry expectation: `NOT_APPLICABLE` unless they send heartbeats. |
| Different per device/metric? | **Yes.** `E` is per `(asset, metric)`. A 15-minute temperature series and an hourly heartbeat on the same asset are judged independently. |
| Source-level liveness | `last_received_at` of the source versus its keepalive interval (default 300 s **[Proposed]**): `STALE` after 2×, `LOST` after 5×. If every asset of a source goes stale together, the cause is the source or connector, not N broken devices. |
| Backfill | Freshness uses `observed_at`. A `BACKFILL` batch of old readings does **not** make an asset fresh. |

Worked example (`E = 900 s`): last reading 20 min ago → `FRESH`; 50 min → `STALE`; 90 min → `MISSING`.

---

## 10. Validation order and rejection codes

Each record is validated in this order; the first failure rejects it.

1. `record_type` known → `UNKNOWN_RECORD_TYPE`
2. Structure: required fields, types, enums → `SCHEMA_INVALID`
3. Record type permitted for the key's scopes → `SCOPE_DENIED`
4. Asset resolves (`ASSET_UNRESOLVED`) and is permitted for the source (`ASSET_NOT_PERMITTED`)
5. Metric / event type known → `UNKNOWN_METRIC` / `UNKNOWN_EVENT_TYPE`
6. Compatible with the asset's system → `METRIC_NOT_COMPATIBLE` / `EVENT_NOT_COMPATIBLE`
7. Unit equals canonical unit → `UNIT_MISMATCH`
8. Timestamp format → `INVALID_TIMESTAMP`; not future → `FUTURE_TIMESTAMP`; not too old → `TIMESTAMP_TOO_OLD`
9. Value finite → `INVALID_VALUE`
10. Size limits → `PAYLOAD_TOO_LARGE`
11. Plausible range: outside `min_plausible`/`max_plausible` ⇒ **stored as `BAD`** with a warning, not rejected
12. Natural key: same content ⇒ `duplicate`; different content ⇒ `CONFLICT`

### 10.1 Record-level reason codes (in `rejected[]`)

| Code | Meaning |
|---|---|
| `UNKNOWN_RECORD_TYPE` | `record_type` is not `telemetry`, `event` or `state`. |
| `SCHEMA_INVALID` | Missing/wrong-typed field or invalid enum. `message` names the field path. |
| `SCOPE_DENIED` | The key lacks the scope for this record type. |
| `ASSET_UNRESOLVED` | `external_id` has no mapping for this source, or `asset_code` unknown. |
| `ASSET_NOT_PERMITTED` | Asset belongs to another building than the source. |
| `UNKNOWN_METRIC` / `UNKNOWN_EVENT_TYPE` | Code not registered. |
| `METRIC_NOT_COMPATIBLE` / `EVENT_NOT_COMPATIBLE` | Valid code, wrong system for this asset. |
| `UNIT_MISMATCH` | Unit differs from the metric's canonical unit. |
| `INVALID_TIMESTAMP` | Not RFC 3339 UTC with `Z`. |
| `FUTURE_TIMESTAMP` | More than 5 min ahead of `received_at`. |
| `TIMESTAMP_TOO_OLD` | Older than the configured maximum back-date. |
| `INVALID_VALUE` | Not a finite number. |
| `PAYLOAD_TOO_LARGE` | `details` or `source_metadata` over 8 KiB. |
| `CONFLICT` | First write wins: same key, different content. |

### 10.2 Batch-level errors (HTTP)

| HTTP | Code | Retry with same `batch_id`? |
|---|---|---|
| 400 | `INVALID_CONTENT_LENGTH` | No — fix the request |
| 401 | `AUTH_INVALID` | No — fix credentials |
| 403 | `SOURCE_MISMATCH`, `SOURCE_DISABLED` | No |
| 409 | `BATCH_ID_REUSED` | No — use a new `batch_id` |
| 413 | `BATCH_TOO_LARGE` | No — split the batch |
| 422 | `UNSUPPORTED_CONTRACT_VERSION`, `UNKNOWN_BUILDING`, all records rejected | No — envelope errors need correction; rejected records are audited |
| 503 | `INGESTION_SCHEMA_NOT_APPLIED`, `SERVICE_UNAVAILABLE` | Retry same batch after migration or transient database recovery |

---

## 11. Authentication and authorization

**Approved for v1 on 18 November 2026:** one rotatable API key per data source, with limited permissions. The key is sent in the Authorization header, stored only as a one-way hash, and bound to exactly one registered source/building. No key or secret is included in the JSON payload.

| Aspect | v1 rule |
|---|---|
| Presentation | `Authorization: Bearer azk_<prefix>_<secret>` |
| Storage | Only a SHA-256 hash of the high-entropy random token is stored in `source_credentials`, plus a short prefix for lookup. The full secret is shown once at provisioning. |
| Binding | A key belongs to exactly one source, and therefore one building. A request whose `source.source_code` differs from the key's source is refused `SOURCE_MISMATCH`. |
| Scopes | `ingest:telemetry`, `ingest:event`, `ingest:state`. Checked per record. |
| Lifecycle | Expiry is optional; revocation is immediate; multiple active keys per source support rotation; `last_used_at` is recorded. |
| Disabled source | `status = DISABLED/ERROR` or revoked key ⇒ `403 SOURCE_DISABLED`. |
| Errors | Never reveal whether a source exists. A bad key is `401 AUTH_INVALID`. |
| Development | Provision a source key with `scripts/ingestion_admin.py`; there is no unauthenticated development bypass. |

**Context.** The existing API, Socket.IO and database port currently have no authentication (ISS-015). Ingestion must be the first protected surface even if the rest is not yet.

---

## 12. Responses

All batch responses share one shape. `rejected[]` lists at most 100 entries; when more exist, `rejected_truncated` is `true` and the complete list is available through `GET /api/v1/ingest/batches/{batch_id}`.

### 12.1 Fully accepted — `200`

```json
{
  "batch_id": "b5c1a6a0-3c5e-4e0e-9a1e-2b7d8c1f0a11",
  "status": "ACCEPTED",
  "received_at": "2026-11-15T09:15:05Z",
  "contract_version": "1.0",
  "counts": { "received": 3, "accepted": 3, "duplicate": 0, "rejected": 0 },
  "results": [
    { "index": 0, "record_type": "telemetry", "outcome": "ACCEPTED", "record_id": 123 },
    { "index": 1, "record_type": "event", "outcome": "ACCEPTED", "record_id": 456 },
    { "index": 2, "record_type": "state", "outcome": "ACCEPTED", "record_id": 789 }
  ],
  "rejected": [],
  "rejected_truncated": false,
  "warnings": [],
  "transaction": "COMMITTED"
}
```

### 12.2 Partially accepted — `200`

```json
{
  "batch_id": "6f1d9c1e-91b4-4b79-8f0e-5a3d7c2e4b10",
  "status": "PARTIAL",
  "received_at": "2026-11-15T09:30:05Z",
  "contract_version": "1.0",
  "counts": { "received": 3, "accepted": 1, "duplicate": 1, "rejected": 1 },
  "results": [
    { "index": 0, "record_type": "telemetry", "outcome": "ACCEPTED", "record_id": 123 },
    { "index": 1, "record_type": "event", "outcome": "DUPLICATE" },
    { "index": 2, "record_type": "telemetry", "outcome": "REJECTED", "reason_code": "UNIT_MISMATCH",
      "message": "unit does not match the canonical unit" }
  ],
  "rejected": [
    { "index": 2, "record_type": "telemetry", "reason_code": "UNIT_MISMATCH",
      "message": "unit does not match the canonical unit" }
  ],
  "rejected_truncated": false,
  "warnings": [ { "index": 0, "code": "UNKNOWN_FIELD", "message": "unknown field 'foo' ignored" } ],
  "transaction": "COMMITTED"
}
```

### 12.3 Nothing accepted — `422`

Same body, `"status": "REJECTED"`, `counts.accepted = 0` and at least one rejection. A batch consisting only of duplicates is **not** this case: it returns `200 ACCEPTED` with `duplicate > 0`.

### 12.4 Batch-level error — `4xx / 5xx`

```json
{
  "error": {
    "code": "SOURCE_MISMATCH",
    "message": "source_code 'vendor-b' does not match the authenticated source",
    "retryable": false
  },
  "batch_id": "b5c1a6a0-3c5e-4e0e-9a1e-2b7d8c1f0a11"
}
```

Database-unavailable `503` responses set `retryable: true`; retry the same `batch_id`. Schema-not-applied `503` requires migration 006 before retrying. A replay of an already committed exact batch returns the saved response with `Idempotent-Replay: true`.

### 12.5 Connector retry rule of thumb

| Response | Connector action |
|---|---|
| `200` (`ACCEPTED` / `PARTIAL`) | Done. Keep rejected records for operator review; do not resend them unchanged. |
| `422`, `400`, `401`, `403`, `409`, `413` | Do not retry blindly. Fix the cause (credentials, mapping, payload, size). |
| Database `503`, network error | Retry the **same batch with the same `batch_id`** and exponential backoff with jitter. For schema-not-applied `503`, wait until migration 006 is deployed. |

---

## 13. Examples

### 13.1 One envelope with all three record types

```json
{
  "contract_version": "1.0",
  "batch_id": "b5c1a6a0-3c5e-4e0e-9a1e-2b7d8c1f0a11",
  "sent_at": "2026-11-15T09:15:04Z",
  "delivery_mode": "LIVE",
  "source": {
    "source_code": "vendor-a-bms-main-tower",
    "connector": {
      "name": "vendor-a-connector",
      "version": "0.3.1"
    },
    "connector_status": {
      "vendor_link": "CONNECTED",
      "buffered_records": 0,
      "dropped_records": 0,
      "last_poll_at": "2026-11-15T09:15:00Z"
    }
  },
  "building_code": "MAIN-TOWER",
  "records": [
    {
      "record_type": "telemetry",
      "asset": {
        "external_id": "TS-114"
      },
      "metric_code": "temperature",
      "value": 24.6,
      "unit": "C",
      "observed_at": "2026-11-15T09:15:00Z",
      "quality": "GOOD",
      "source_metadata": {
        "original_value": 76.28,
        "original_unit": "F"
      }
    },
    {
      "record_type": "event",
      "source_event_id": "evt-000184233",
      "asset": {
        "asset_code": "ACS-RDR-02"
      },
      "event_type": "door_forced",
      "observed_at": "2026-11-15T09:21:47Z",
      "severity": "CRITICAL",
      "details": {
        "note": "door forced"
      }
    },
    {
      "record_type": "state",
      "asset": {
        "asset_code": "CCTV-CAM-02"
      },
      "observed_at": "2026-11-15T09:20:11Z",
      "availability": "UNAVAILABLE",
      "reported_state": "FAULT",
      "reason_code": "VIDEO_SIGNAL_LOST",
      "message": "no video signal"
    }
  ]
}
```

### 13.2 Two vendors, one canonical form

Vendor A sends Fahrenheit and epoch seconds, Vendor B sends `"Celsius"` and a `+02:00` offset. Both connectors emit the **same** canonical record (values illustrative):

```json
{
  "record_type": "telemetry",
  "asset": {
    "external_id": "TS-114"
  },
  "metric_code": "temperature",
  "value": 24.6,
  "unit": "C",
  "observed_at": "2026-11-15T09:15:00Z",
  "quality": "GOOD",
  "source_metadata": {
    "original_value": 76.28,
    "original_unit": "F"
  }
}
```

### 13.3 Conformance vectors

A connector is not considered ready until its tests reproduce these outcomes against the Core's validation:

| # | Input | Expected |
|---|---|---|
| 1 | Valid envelope, 3 records | `200 ACCEPTED`, `accepted = 3` |
| 2 | Same envelope resent, same `batch_id` | `200`, stored result, `Idempotent-Replay: true` |
| 3 | Same `batch_id`, one value changed | `409 BATCH_ID_REUSED` |
| 4 | Same telemetry key, same value, new batch | `duplicate = 1` |
| 5 | Same telemetry key, different value, new batch | `CONFLICT`, first value kept |
| 6 | `observed_at` = `2026-11-15T11:15:00+02:00` | `INVALID_TIMESTAMP` |
| 7 | `observed_at` 10 min in the future | `FUTURE_TIMESTAMP` |
| 8 | `door_forced` for a camera | `EVENT_NOT_COMPATIBLE` |
| 9 | `temperature` with unit `F` | `UNIT_MISMATCH` |
| 10 | `external_id` not mapped | `ASSET_UNRESOLVED`, payload kept for review |
| 11 | `quality: "MISSING"` | `SCHEMA_INVALID` |
| 12 | `state` with only `communication_status` | `SCHEMA_INVALID` |
| 13 | 1000 records, 10 invalid | `PARTIAL`, `accepted = 990`, `rejected = 10` |
| 14 | 5001 records | `413 BATCH_TOO_LARGE` |
| 15 | Wrong `source_code` for the key | `403 SOURCE_MISMATCH` |
| 16 | 0 records, valid key | `200`, source liveness updated |

Vectors 1, 3, 6–9, 11, 12, 14 were exercised against the JSON Schemas in this document with a throw-away checker (schema-level outcomes only). Everything that needs a database (2, 4, 5, 10, 13, 15, 16) is **untested** until the ingestion service exists.

---

## 14. Amendments this contract makes to `CANONICAL_DATA_MODEL.md`

| Canonical text | Change | Reason |
|---|---|---|
| §13 and §16.6: `quality` includes `MISSING`; `data_gaps.cause = 'SOURCE_DECLARED_MISSING'` | Remove `MISSING` from quality; remove the `SOURCE_DECLARED_MISSING` cause | D1 |
| §16.3 state example sends `communication_status` | State records carry only `reported_state` and `availability` | D2 |
| §8 `operational_state` / `communication_status` as connector-reportable | Core-derived only | D2 |
| §16.6 `source_event_id`: "required for connectors (recommended)" | Required | Avoid collapsing distinct events |
| §16.6 events: optional `value`, `unit` | Removed from event records; use `details` | Simplicity |
| §6 optional Core-side `unit_conversions` | Deferred; exact canonical unit only | Smaller v1 |
| §16.5 response shape | Replaced by §12 | Adds `status`, counts, retry semantics |
| Schema comment "1..5000 atomic batch" | `0..5000`, **partial acceptance** | D4 |
| §17 route name | `/api/v1/ingest/batches` | Approved and implemented |

---

## 15. Decision register

| ID | Question | Decision | Status |
|---|---|---|---|
| D1 | Is `MISSING` a quality value? | No. Three qualities only; missing data is detected separately. | Decided |
| D2 | Connector sends state, or Core infers? | Connector reports what it knows; Core computes freshness; no `OFFLINE` from silence. | Decided |
| D3 | Same key, different values? | First write wins; later record rejected and logged. | Decided |
| D4 | One bad record in a batch? | Partial acceptance with per-record reasons. | Decided |
| D5 | Connector authentication? | Hashed bearer API key per source, scoped, revocable, and rotatable. | Decided and implemented |
| P1 | `event_time` as a separate field? | No; `observed_at` is the event time. | Proposed |
| P2 | Endpoint layout | Single batch endpoint; per-type endpoints deferred. | Proposed |
| P3 | Empty batch | Allowed as keepalive. | Proposed |
| P4 | Unmapped external asset | Reject `ASSET_UNRESOLVED`; no auto-create. | Proposed |
| P5 | Unit conversion in Core | Deferred; connector converts. | Proposed |
| P6 | `source_event_id` | Required for events. | Proposed |
| P7 | Maximum back-date | No age cutoff in v1; natural-key conflict handling still applies. | Decided |
| P8 | Path prefix `/api/v1` | `/api/v1/ingest/batches` and source-scoped batch lookup. | Decided and implemented |
| P9 | Sanctioned value corrections | Not in v1. | Open |
| P10 | Deprecation window for old MAJOR | Not defined. | Open |
