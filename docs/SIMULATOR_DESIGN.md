# Reference Simulator Connector — design proposal

**Phase 1 item 1.2. Decision: APPROVED (recommendations accepted), 2026-11-20.** D2 implements the accepted recommendations in this note. D3 remains separate and is not authorized by this package.

## 1. Purpose and boundary

The reference simulator is a separate package under `connectors/`. It emits deterministic demo telemetry, events, and selected equipment-state observations in the Core's canonical format. It is the example other connectors can copy and is intended to replace the demo's direct insertion of generated facts into PostgreSQL.

The simulator talks to Core **only** through the HTTP endpoints in [`INGESTION_CONTRACT.md`](INGESTION_CONTRACT.md). It has no database credentials, database driver, Core-internal IDs, or imports from `app/`. It does not provision Core sources or mappings; an operator does that with the existing administration process. It does not control equipment, infer an offline state from silence, calculate alerts or incidents, alter Core schemas, or replace the production seed's schema/reference catalogues.

The contract and existing connector architecture are authoritative for wire behavior: [`CONNECTOR_ARCHITECTURE.md`](CONNECTOR_ARCHITECTURE.md) describes the connector boundary and lifecycle, while §§2–12 of the ingestion contract define the protocol.

## 2. Contract mapping

### HTTP interface and envelope

| Contract item | Simulator behavior |
|---|---|
| `POST /api/v1/ingest/batches` (§2) | Send a complete envelope containing zero or more records. There are no per-record endpoints. |
| `GET /api/v1/ingest/batches/{batch_id}` (§2, §12) | On operator request, retrieve the stored outcome and complete rejection list for a batch. Use the same source credential. |
| Request headers (§2, §11) | `Authorization: Bearer <source API key>` and `Content-Type: application/json; charset=utf-8`. TLS is required outside local development. Never put credentials in the body or logs. The contract does not require an `Accept` header. |
| Required envelope fields (§4) | `contract_version` (`"1.0"`), `batch_id` (UUID), `sent_at` (UTC `Z`, diagnostic only), `source` (`source_code`, `connector.name`, `connector.version`), `building_code`, and `records`. |
| Optional envelope fields (§4) | `delivery_mode` (`LIVE` or `BACKFILL`, default `LIVE`) and `source.connector_status`. The latter is an optional diagnostics object, at most 8 KiB. |
| Request limits (§2, §8.1) | At most 5 MiB and 5000 records. The contract recommends batches around 500–1000 records. `details` and `source_metadata` are each limited to 8 KiB. Split on both record count and serialized byte size. |

The simulator uses source-scoped external IDs, for example a configured stable ID for each simulated device. Those IDs must be mapped by an operator to existing `asset_code` values for the source and building (§6). The simulator never sends `asset_id`, `metric_id`, or other Core IDs. `building_code` and mapping values must be supplied by the deployment; the SQL seed's human-readable building names do not establish external-safe codes.

### Record types and fields (§5)

- **Telemetry**: required `record_type: "telemetry"`, `asset` (exactly one of `asset_code` or `external_id`), `metric_code`, finite numeric `value`, canonical `unit`, and `observed_at`; optional `quality` (`GOOD`, `UNCERTAIN`, `BAD`, default `GOOD`) and `source_metadata`.
- **Event**: required `record_type: "event"`, stable `source_event_id`, `asset`, `event_type`, and `observed_at`; optional `severity` (`INFO`, `WARNING`, `CRITICAL`, default `INFO`), `details`, and `source_metadata`.
- **Equipment state**: required `record_type: "state"`, `asset`, `observed_at`, and at least one of `reported_state` (`RUNNING`, `STOPPED`, `STANDBY`, `FAULT`, `UNKNOWN`) or `availability` (`AVAILABLE`, `UNAVAILABLE`, `UNKNOWN`); optional `reason_code`, `message` (≤500 characters), and `source_metadata`.

All timestamps sent as `observed_at` or `sent_at` are UTC with a trailing `Z` (§3). `sent_at` never substitutes for observation time. Unknown extra fields are ignored by the Core with a response warning; unknown enum values and unknown `record_type` values are rejected (§2). A simulator silence scenario omits records; it does not manufacture `OFFLINE`, `UNAVAILABLE`, `MISSING` quality, or Core-derived `communication_status`, `freshness`, or `operational_state` (§5, §7, §9).

### Idempotency, responses, and retry decisions (§8, §10.2, §12)

The idempotency key is `(authenticated source, batch_id)`. An exact replay of the same body returns the stored response with `Idempotent-Replay: true` and no side effects. Reusing that ID with changed bytes/content returns `409 BATCH_ID_REUSED`. Same-natural-key records can independently return `duplicate`; changed content is rejected `CONFLICT` and the original measurement remains authoritative. The connector must persist and resend the exact same payload and `batch_id` whenever it retries.

For response parsing, v1 record rejection codes are `UNKNOWN_RECORD_TYPE`, `SCHEMA_INVALID`, `SCOPE_DENIED`, `ASSET_UNRESOLVED`, `ASSET_NOT_PERMITTED`, `UNKNOWN_METRIC`, `UNKNOWN_EVENT_TYPE`, `METRIC_NOT_COMPATIBLE`, `EVENT_NOT_COMPATIBLE`, `UNIT_MISMATCH`, `INVALID_TIMESTAMP`, `FUTURE_TIMESTAMP`, `TIMESTAMP_TOO_OLD`, `INVALID_VALUE`, `PAYLOAD_TOO_LARGE`, and `CONFLICT` (§10.1). A value outside configured plausibility bounds is stored with `quality=BAD` and warning `OUT_OF_PLAUSIBLE_RANGE`, rather than rejected (§7, §10). Batch-level codes used by this connector are `INVALID_CONTENT_LENGTH`, `AUTH_MISSING`, `AUTH_INVALID`, `SOURCE_MISMATCH`, `SOURCE_DISABLED`, `BATCH_ID_REUSED`, `BATCH_TOO_LARGE`, `RATE_LIMITED`, `UNSUPPORTED_CONTRACT_VERSION`, `UNKNOWN_BUILDING`, `INGESTION_SCHEMA_NOT_APPLIED`, and `SERVICE_UNAVAILABLE` (§10.2). Request-model validation can also return HTTP `422 SCHEMA_INVALID` (§10.2). The simulator should preserve the returned code/message and never infer acceptance from an HTTP status alone.

| Core response | Required connector action |
|---|---|
| `200 ACCEPTED` or `200 PARTIAL` | Treat the batch transaction as committed (`transaction: COMMITTED`). Read `counts.received`, `accepted`, `duplicate`, and `rejected` plus per-record `results`. `rejected[]` is capped at 100 with `rejected_truncated`; use the GET endpoint for the full list. Mark the batch delivered. Preserve rejected details for operator review; do not resend rejected records unchanged. A corrected record belongs in a new batch with a new ID. |
| `401 AUTH_MISSING` / `AUTH_INVALID`; `403 SOURCE_MISMATCH` / `SOURCE_DISABLED` | Stop sending and stop the automatic retry loop. Keep the durable queue. Raise a credential/source configuration fault without logging the key. Resume only after operator correction. |
| `409 BATCH_ID_REUSED`; `400 INVALID_CONTENT_LENGTH`; `413 BATCH_TOO_LARGE` | Terminal for that body. Preserve it for diagnosis; correct the payload/size or the ID/payload bookkeeping before sending a new batch. Never silently replace the payload for an existing ID. |
| `422` including `SCHEMA_INVALID`, `UNSUPPORTED_CONTRACT_VERSION`, `UNKNOWN_BUILDING`, or all records rejected | Do not blindly retry. A `422 REJECTED` means rejection audit was committed, not that the server did nothing. Preserve the response and rejected records; operator correction produces a new batch ID. |
| `429 RATE_LIMITED` | Retry the exact body and ID no sooner than the integer `Retry-After` value. The contract marks this retryable and requires honoring the header. Ingestion limiting is per source credential, after auth; current implementation is in-memory per API process (§10.2). |
| `503 SERVICE_UNAVAILABLE` | Retry the same body and ID with backoff. For `INGESTION_SCHEMA_NOT_APPLIED`, pause until the required migrations are applied, then retry. Network failures and timeouts also retry the same batch (§8.2, §12.4–12.5). |
| Other `5xx` | Approved behavior: treat as transient and retry the unchanged batch. `CONNECTOR_ARCHITECTURE.md` §6 proposes retrying `500`; the contract explicitly defines retryability for `503` and network errors but does not specify every other `5xx`. D3 implements the approved retry behavior; this implementation note does not change contract semantics. |

The HTTP limiter currently defaults to 60 requests/minute and burst capacity 10 per source credential/process. The simulator should use a single in-flight request initially and let the Core's `429` plus `Retry-After` control pacing; it must not attempt to infer or probe another source's budget.

### `connector_status` and keepalive

`source.connector_status` (§4) is optional diagnostics with fields such as `vendor_link` (`CONNECTED`, `DISCONNECTED`, `UNKNOWN`), `buffered_records` (non-negative integer), `dropped_records` (non-negative integer), and `last_poll_at`. It is stored with the batch; it is not an equipment-state report. The simulator should report observed link/buffer/drop diagnostics and must not claim a vendor link state that it did not measure.

An empty `records: []` batch is a keepalive (§4, §8.1): it updates source liveness and carries no measurement or event. Give each new keepalive a fresh `batch_id`; retry a particular keepalive only with its original ID/body. The contract's 300-second source keepalive interval is marked **Proposed**, not a settled customer setting (§9); make the interval configurable and ask the human to confirm it.

## 3. Deterministic data and scenarios

### Seed-derived coverage

The current synthetic catalogue and point series are in [`db/init/anzma_schema_and_seed.sql`](../db/init/anzma_schema_and_seed.sql), in the asset, metric/event catalogue, and telemetry `VALUES` sections. It defines 12 assets and these 15 telemetry points:

The seed's location/building references place `HVAC-CRAC-01`, `NET-SW-02`, and `CCTV-CAM-02` in Data Center Annex; the other nine assets are in Main Tower. A simulator instance selects one seed building so its emitted source and external-ID mappings stay within the contract's one-building source scope (§4, §6). These site labels are fixture groupings from the checked-in seed, not Core `building_code` values.

| Seed asset code | Simulated metrics (canonical units from the seed catalogue) |
|---|---|
| `HVAC-AHU-01` | `temperature` C, `humidity` %, `power_kw` kW |
| `HVAC-CRAC-01` | `temperature` C, `power_kw` kW |
| `HVAC-CHL-01` | `temperature` C, `power_kw` kW |
| `NET-SW-01` | `cpu_load` %, `packet_loss` %, `bandwidth_util` % |
| `NET-AP-01` | `bandwidth_util` % |
| `NET-SW-02` | `cpu_load` % |
| `CCTV-CAM-01`, `CCTV-CAM-02` | `camera_fps` fps |
| `FIRE-SMK-01` | `smoke_level` %/m |

The seed creates an hourly `heartbeat` event for every asset, subject to the camera-02 silence window. Its other seeded events cover `door_forced`, `door_held_open`, `access_denied`, `link_down`/`link_up`, `camera_offline`/`camera_online`, `hvac_fault`/`hvac_recovered`, and `fire_alarm_triggered`/`alarm_cleared`. These are the catalogued names; the simulator must observe compatibility rules for the mapped system (§5), not emit every event type on every asset. The fire panel and access readers have no telemetry point in the current seed; the simulator must not invent one merely to make coverage look uniform.

### Generator and repeatability proposal

Use a local seeded pseudo-random generator, independent of database state and Python's process-global random source. The seed SQL currently sets `setseed(0.42)`, anchors to the current 15-minute grid, generates 78 hours of 15-minute telemetry, computes a daily `base + amplitude × sin(UTC day phase) + jitter` value, applies listed fault offsets, clamps at zero, and rounds to two decimal places. The simulator proposal is to preserve the point parameters and broad curve/fault behavior, replace SQL `random()` with a versioned deterministic PRNG, and serialize values at two-decimal precision with a stable canonical JSON encoding.

The injected clock is captured once in a scenario manifest and reused after restart; generators never call wall-clock time per record. The manifest includes the scenario ID, seed, UTC clock anchor, generator version, and fixed record ordering. Thus the same seed and scenario manifest produce byte-identical record payloads and batch order. For tests, inject/freeze the clock; advancing the anchor intentionally creates a different scenario manifest. Keep a golden payload test to detect accidental output changes.

Use stable ordering by `observed_at`, record type, external asset ID, metric/event key, then stable event ID. Chunk that order with a fixed configured target (proposed default 500 records, hard maximum 5000 and 5 MiB). Generate `source_event_id` deterministically from the source/scenario identity and event's logical key so a restart does not turn the same event into a new event. `batch_id` is fixed when a chunk is sealed (see §4).

### Selectable scenarios

| Scenario selector | Deterministic behavior |
|---|---|
| `normal` | Emit the seed-derived point series and heartbeat schedule. Continue at the configured 15-minute telemetry cadence; values use the seeded formula above. |
| `fault` | Reproduce the seed's CRAC temperature +9 C interval (anchor −1260 to −1170 minutes) and `NET-SW-01` packet-loss +4 percentage-point interval (−1860 to −1830 minutes), plus the corresponding seeded fault/recovery events. These windows and assets come directly from the seed SQL. |
| `stale-silent` | Omit `CCTV-CAM-02` `camera_fps` samples and heartbeat for the selected interval, following the seed's camera-02 gaps (−2700 to −2640 minutes and the most recent five hours). Do not emit a synthetic offline/unavailable state from silence. |
| `bad-record` | Explicit test-only records exercise such cases as Fahrenheit for a Celsius point, `quality: MISSING`, or an incompatible event. Keep the original rejection; only a corrected record in a new batch may be sent. Allow both mixed valid/invalid and all-invalid forms. |
| `late-out-of-order` | Emit known observations in a deliberately non-arrival order, set `delivery_mode: BACKFILL`, and keep `observed_at` within the configured source back-date limit (contract default 90 days unless overridden). On recovery, deliver oldest queued batch first. |
| `duplicate-batch` | Resend one stored envelope byte-for-byte with its original `batch_id`; assert the stored response and `Idempotent-Replay: true`. This is not a new batch with the same records. |

Fault and bad-record scenarios should require an explicit selector; normal mode must not inject failures accidentally. The event names and source point values remain data-driven from a versioned simulator fixture derived from the current seed, not duplicated as hidden constants across modules.

## 4. Batch ID durability and restart behavior

When a deterministic chunk is sealed, the simulator creates the complete envelope, canonical serialized payload, payload hash, batch sequence, and `batch_id`. It must commit all of them to the local durable outbox **before the first HTTP send**. The stored bytes are immutable. A retry reads the persisted bytes; it does not reconstruct the body from current configuration or regenerate timestamps.

```text
collect → normalize → sort/chunk → assign batch_id → persist+fsync/commit → POST
                                                                  │
                                              response/timeout ──┘ retry exact bytes
```

Crash case: batch 17 is committed locally with ID `B17` and payload `P17`; Core commits it but the response is lost; the simulator process crashes before marking it complete. On restart, the outbox returns `B17/P17` as the oldest pending row. The connector posts the same ID and bytes; Core returns its saved response as an idempotent replay. Only after a definitive successful response does the connector mark that row delivered. Never generate a replacement ID for an ambiguous timeout.

## 5. Durable-buffer options

These are design comparisons, not behavior already implemented in the repository.

| Option | Atomicity and ordering | Crash/corruption behavior | Size cap and overflow handling |
|---|---|---|---|
| SQLite file | Transaction can atomically persist batch ID, exact body, sequence and state; indexed sequence supports oldest-first drain. | Proposed WAL mode with `synchronous=FULL`; transaction recovery is built in and an integrity check can detect corruption. Keep the damaged file for diagnosis and fail closed rather than silently reinitialize. | Enforce a configurable byte cap before accepting more records. Block/pause acquisition when full; do not delete queued batches. Persist any unavoidable dropped count for the next `connector_status`. |
| Append-only JSONL + `fsync` | One writer can append sequence-ordered records; a record is not durable until flush + fsync. A torn final line needs explicit recovery rules. | Detect/truncate only a proven incomplete tail; malformed interior data should quarantine and stop, not skip. Concurrent writers and state updates are harder. | Cap file bytes; rotation/compaction must preserve order and durable state. On full, block acquisition and persist/report any loss counter separately. |
| One file per batch | Write a temporary file, fsync, atomically rename to a sequence-named ready file; directory listing can drain in order. | Simple isolation per batch; partial temp files can be quarantined. Must fsync the directory for rename durability and recover sequence/state consistently. Many files can complicate cleanup and corruption checks. | Cap total bytes/files. Block acquisition at cap; never remove oldest pending data silently. Report unavoidable drops in the next batch/keepalive diagnostics. |

**Approved decision: SQLite outbox.** It best matches the atomic requirement to persist an immutable body, ID, sequence, and delivery state together. The accepted cap is 256 MiB per connector instance; at cap, pause simulator generation and retain pending batches rather than drop them. If an input cannot be queued despite back-pressure, persist an incremented dropped count and report it in the next accepted batch/keepalive's `source.connector_status.dropped_records`. A corrupt outbox must stop sending/acquisition and require operator action; never silently create an empty replacement. Capacity enforcement, overflow accounting, and corruption recovery are D3 work.

## 6. Retry, concurrency, and outage drain

**Approved decision (D3):** one in-flight batch per source, strict oldest-sequence-first delivery, exponential backoff starting at 1 second, doubling to a 5-minute cap, with full jitter. These starting/cap values match the proposal in [`CONNECTOR_ARCHITECTURE.md`, §6](CONNECTOR_ARCHITECTURE.md#6-errors-retry-and-store-and-forward). For `429`, wait at least `Retry-After` seconds (use the larger of that delay and the jittered backoff). Reset backoff after a successful response. Keep older batches ahead of newer live data; label old observations `BACKFILL`. D2 records retryable responses as pending and exits without a retry loop.

An exact retry is not a duplicate write: the source+`batch_id` and immutable payload reproduce the Core's stored transaction result. Natural-key duplicate records are reported in `counts.duplicate`; the connector marks the batch complete. `200 PARTIAL` is complete for delivery, while rejected rows are stored for review and never retried unchanged. `401`/`403`, `409`, and permanent `4xx` do not enter the retry loop. Max in-flight is one until measurements demonstrate an ordering need; if later raised, each source's committed sequence still drains oldest-first.

## 7. Configuration and secret handling

**Proposed first-release configuration:** environment variables for deployment/secret values and a non-secret config file for repeatable scenario settings. Suggested names (all proposals):

| Setting | Use |
|---|---|
| `ANZMA_SIMULATOR_CORE_URL` | Core base URL; require HTTPS outside local development. |
| `ANZMA_SIMULATOR_SOURCE_CODE`, `ANZMA_SIMULATOR_BUILDING_CODE` | Provisioned source/building identifiers. |
| `ANZMA_SIMULATOR_SEED_BUILDING` | Seed catalogue group (`main-tower` or `data-center-annex`) matching the provisioned source's building. |
| `ANZMA_SIMULATOR_CONNECTOR_NAME`, `ANZMA_SIMULATOR_CONNECTOR_VERSION` | Envelope identity. |
| `ANZMA_SIMULATOR_API_KEY` **or** `ANZMA_SIMULATOR_API_KEY_FILE` | Credential source; exactly one configured. Neither is logged or included in telemetry, buffered bodies, errors, or process output. |
| `ANZMA_SIMULATOR_SEED`, `ANZMA_SIMULATOR_SCENARIO`, `ANZMA_SIMULATOR_CLOCK_ANCHOR` | Deterministic scenario manifest. |
| `ANZMA_SIMULATOR_INTERVAL_SECONDS`, `ANZMA_SIMULATOR_BATCH_RECORDS`, `ANZMA_SIMULATOR_BUFFER_MAX_BYTES` | Polling cadence, chunk target, and durable-buffer cap. |

The current contract uses one rotatable API key per source, SHA-256 hash storage in Core, and multiple active credentials during rotation (§11). On `401 AUTH_INVALID` or `403 SOURCE_DISABLED`, the simulator pauses and preserves the outbox; it does not loop with the rejected key. An operator supplies the replacement key out-of-band (environment/secret file), then restarts/resumes. Pending bodies retain their original IDs and bytes. The simulator never logs the key or its full Authorization header. The operator must ensure the replacement key is for the same source and has the required telemetry/event/state scopes.

## 8. Packaging proposal

Proposed isolated layout:

```text
connectors/reference_simulator/
  Dockerfile
  README.md
  simulator/
    __init__.py
    config.py
    records.py
    outbox.py
    client.py
    cli.py
  tests/
    test_simulator.py
```

The CLI entry point is `python -m simulator.cli`. This initial layout is eight files. The Dockerfile should run as a non-root user, mount the SQLite outbox at an operator-selected persistent path, and provide no Core source tree or database network credential. Standard library `sqlite3`, deterministic generation, and HTTP client choices should be confirmed during D2; no dependency or image is introduced in D1.

**Compose decision proposal:** keep it out of the default application startup. If Compose integration is approved later, make it an explicitly enabled optional `simulator` profile (or separate Compose file) with its own source key, config, and persistent outbox. It must never start by default. This package does not modify `deploy/`.

## 9. Legacy paths and parity before retirement

The repository search found one actual demo data seeder and these related paths:

| Path | Verified role | D1 retirement boundary |
|---|---|---|
| [`db/init/anzma_schema_and_seed.sql`](../db/init/anzma_schema_and_seed.sql) | Initializes schema/reference catalogues and directly inserts synthetic telemetry, heartbeat, and event history. Its data section is the demo data the simulator could replace. | Do not remove its schema/reference data. Retire or make optional only the direct demo-fact seed after parity approval; no change now. |
| [`tests/scenarios/e2e_s1.py`](../tests/scenarios/e2e_s1.py) | S1 evidence runner; `--build` creates a test database and loads the seed before checking the seeded CRAC fault. It is not a separate connector or vendor injector. | Keep as a regression/evidence consumer until an equivalent end-to-end simulator scenario passes; do not copy its `app/` imports or database access into the simulator. |
| [`tests/integration/test_day2_integration.py`](../tests/integration/test_day2_integration.py), [`tests/integration/test_frontend_api.py`](../tests/integration/test_frontend_api.py), [`tests/integration/test_data_integrity.py`](../tests/integration/test_data_integrity.py) | Test-only database fixtures and conformance/compatibility checks; `test_frontend_api.py` also exercises the legacy `POST /events`/`POST /alerts` routes and the 16 contract vectors. | These are tests, not demo seed paths. Keep them and their assertions; D2/D3 add connector tests rather than retire them. |
| `POST /events` in [`app/main.py`](../app/main.py) | Existing legacy HTTP event route, tested for compatibility. It is not a simulator package. | Do not remove or repurpose it in this design. Any later retirement needs a separate approved compatibility plan. |

The administration CLI [`scripts/ingestion_admin.py`](../scripts/ingestion_admin.py) provisions sources, rotates/revokes keys, and maps assets; it is not a data seeder and is not replaced by the simulator.

Before retiring the direct demo telemetry/event seed, require parity evidence on a fresh isolated database: (1) same asset/metric/event coverage and deterministic scenario time windows, (2) matching fault/recovery observations and expected S1 alert/incident/evidence behavior, (3) all 16 §13.3 vectors still pass, (4) source liveness/keepalive and stale-silence behavior are correct, (5) restart/replay and outage drain show no duplicate fact writes or lost batches, (6) API/dashboard outputs preserve their expected behavior, and (7) no test or package imports `app/` or accesses PostgreSQL from the simulator. Keep schema/reference bootstrapping and existing regression tests intact.

## 10. D2/D3 test plan

| Test group | Coverage | Needs running Core? | CI-like isolation |
|---|---|---|---|
| Deterministic generator and golden bytes | Fixed seed/scenario/clock; point list, units, event IDs, ordering, stable batch sequence. | No. | Unit tests with an injected frozen clock and temporary files. |
| Persist-before-send and crash/restart | Batch ID/body exist before transport; simulate crash after Core commit/before local ack; restart resends exact bytes. | Mock transport for state-machine test; real Core for replay proof. | Temporary SQLite file for unit test; optional isolated Core integration service for replay proof. |
| Contract conformance | Reuse all applicable §13.3 cases: 1 accepted mixed record types, 2 exact replay, 3 changed-body ID reuse, 4 duplicate natural key, 5 immutable conflict, 6–7 timestamp rules, 8 compatibility, 9 unit, 10 mapping, 11–12 schema, 13 partial counts, 14 batch limit, 15 source mismatch, 16 empty keepalive. | Yes for HTTP/Core parity. | Start the API and PostgreSQL in a unique disposable project/database with test-only source/key/mappings; never point CI at a shared or live DB. |
| Outage then drain | Core unavailable, durable queue grows, recovery drains oldest-first, `BACKFILL` is used, exact retries produce no duplicate records, and accepted/duplicate/rejected totals match. | Mock Core for deterministic transport faults; isolated real Core for idempotency/count proof. | Temporary database volume and unique Compose project; inspect persisted rows and batch outcomes. |
| Buffer cap/corruption | Cap behavior blocks rather than silently discards; detectable corruption stops sending and is reported. | No for local store tests; Core needed only to verify subsequent `connector_status`. | Temporary filesystem and outbox copy; no shared data. |
| Rate limit | Receive `429`, obey `Retry-After`, keep batch ID/body and later deliver; verify another source is not used. | Yes for Core limiter integration; mock response for client logic. | Isolated API process/source credentials and fake clock where possible; no sleeps in unit tests. |
| Rotation/auth | `401`/`403` pauses without a retry loop; operator replacement resumes same stored body/ID. | Mock for pause/resume; Core integration for real rotated key. | Temporary secret file and disposable source/key; assert captured logs omit key bytes. |

`tests/integration/test_frontend_api.py` is the existing DB-backed Core conformance suite and is the source of the current vector assertions. It remains unchanged; the simulator's own transport/generator tests should not weaken or duplicate away Core assertions. Tests against a live Core require an explicitly started isolated test instance; pure generator, store, and retry state-machine tests do not.

## 11. Work split

The split below is deliberately limited to small packages, each targeting **10 files or fewer**; actual files depend on the human's decisions.

**D2 — structure, deterministic records, persisted batch IDs, one-shot HTTP send (≤10 files):**

1. Add the separate package and non-root Dockerfile/README entry point.
2. Implement config validation, seed-derived deterministic record/scenario generation, canonical serialization, and stable chunk ordering.
3. Implement the minimal transactional SQLite outbox needed to persist `batch_id`, body, sequence, and status before send.
4. Implement the HTTP client for the two contract endpoints, auth header redaction, one-shot response parsing, and explicit terminal handling for 4xx.
5. Add generator golden tests and persistence-before-send/restart unit tests. Do not wire a default Compose startup.

**D3 — durable-buffer hardening, retries/backoff, outage tests (≤10 files):**

1. Add bounded-capacity and corruption-recovery policy to the outbox, with persisted `connector_status` loss/buffer diagnostics.
2. Add single-flight FIFO drain, exponential backoff/jitter, `Retry-After`, and paused credential behavior.
3. Add deterministic simulated HTTP failure tests, crash-restart/outage-drain integration tests, and a Core-backed 429/rate-limit test.
4. Add optional deployment integration only after approval; keep it disabled by default. No D1/D2/D3 change is authorized by this note.

## 12. Approved decisions and runtime inputs

**Decision: APPROVED (recommendations accepted), 2026-11-20.** The choices below apply within the D2/D3 split:

| Decision | Approved choice |
|---|---|
| Demo fact scope | Mirror the 15 telemetry points and hourly heartbeat/event families in the current seed; keep schema/reference catalogues. |
| Source identity/mappings | One explicitly provisioned source per simulator instance/building; use stable source-scoped external IDs mapped by an operator. Human must supply source/building codes. |
| Determinism | Persist a scenario manifest containing seed, scenario, clock anchor, and generator version; same manifest yields byte-identical payloads. |
| Store | SQLite transactional outbox, initially capped at a proposed 256 MiB; pause acquisition at capacity and never drop queued batches. |
| Retry | One in-flight batch, oldest-first; 1 s exponential backoff with full jitter up to 5 min; honor `Retry-After`, retry other `5xx` as transient. D3 implements this. |
| Keepalive/status | Configurable interval based on the contract's 300 s **Proposed** value; include `connector_status` only from observable simulator state. |
| Test scenarios | Normal by default; fault, stale/silent, bad-record, late/out-of-order, and duplicate-batch scenarios require explicit selection. |
| Packaging | Separate Python package and non-root image; recommend an opt-in simulator profile only if deployment integration is later approved. |
| Legacy retirement | Retire only direct demo-fact seeding after the parity gates in §9; keep schema/reference data, test fixtures, and legacy endpoints until separately approved. |

Deployment-specific source/building codes and mappings remain operator configuration. D2/D3 will mirror seed event families, use the approved 256 MiB SQLite cap with pause/no-drop behavior, configurable 300-second keepalive default, retry policy in §6, fixed-clock manifest determinism, and no Compose profile or default startup. D2 deliberately excludes the storage-cap and retry implementation assigned to D3.
