# Freshness, source health, gaps, and building schedules — design note

**Phase 1 item 1.3. Decision: APPROVED with amendments, 2026-11-20.** The human approved this design with amendments A1–A6: expectation scopes follow contract §9 only (`ASSET_METRIC`, `ASSET_TYPE_METRIC`, `SOURCE`, `GLOBAL`); freshness is always-on and has no work-hours gating; a gap opens when freshness becomes `MISSING` with `gap_start=last_observed_at` and closes at the first new observation; backfill-driven gap splitting is deferred; freshness creates no alerts/incidents; only non-working weekdays are stored; building timezone/schedule columns remain nullable with existing rows backfilled to parity defaults, and future application code must use those defaults plus one warning when values are NULL. `buildings.building_code NOT NULL` remains item 1.6.

## 1. Verified current state

- Migration 006 defines `data_sources.last_received_at`, `last_success_at`, `last_error_at`, and `last_error` ([006, lines 10–24](../db/migrations/006_ingestion_foundation.sql#L10)). The ingestion path updates `last_received_at` for a received batch; advances `last_success_at` for accepted/duplicate records or an empty batch; and records the first rejection as a bounded error ([ingestion.py, lines 458–473](../app/services/ingestion.py#L458)). This is source/batch bookkeeping, not per-series freshness.
- Migration 008 adds `ingestion_batches.connector_status` JSONB ([008, lines 8–13](../db/migrations/008_ingestion_contract_conformance.sql#L8)); the contract says it is optional connector diagnostics, limited to 8 KiB, and persisted on the batch ([INGESTION_CONTRACT.md §4](INGESTION_CONTRACT.md#L419)). It is not an independently evaluated health state.
- There is no implemented freshness evaluator/job or persisted `reporting_expectations`, `data_gaps`, or current-freshness table found under `app/`; historian documentation marks those as future work ([HISTORIAN_DESIGN.md, lines 5–6, 22](HISTORIAN_DESIGN.md#L5)). Existing `equipment_state_records` stores connector-reported state evidence, not Core-derived freshness ([006, lines 140–153](../db/migrations/006_ingestion_foundation.sql#L140)).
- Fixed time assumptions are [`alert_config.py` lines 7–9](../app/domain/alerts/alert_config.py#L7): offset `+2`, work start `8`, work end `18`. [`alert_engine.py` lines 34–39](../app/domain/alerts/alert_engine.py#L34) adds two hours to a timestamp and treats `[08:00, 18:00)` as occupied. It affects `ACC_DOOR_FORCED`, `ACC_DENIAL_SPIKE`, `SEC_BLIND_SPOT_ACCESS`, and working/off-hours anomaly regimes ([alert_engine.py, lines 282–315, 335–347, 397–425](../app/domain/alerts/alert_engine.py#L282)). `CCTV_AFTER_HOURS_MOTION` consumes the event's stored `details.after_hours` ([alert_engine.py, lines 348–358](../app/domain/alerts/alert_engine.py#L348)); it does not recompute that field there.
- The dashboard's `no_data` asset status is a separate, period/coverage-based KPI ([dashboard_core_pg.py, lines 437–449](../app/repositories/dashboard_core_pg.py#L437)); heartbeat compliance assumes one heartbeat opportunity per hour ([dashboard_core_pg.py, lines 681–725](../app/repositories/dashboard_core_pg.py#L681)). These do not use `LOCAL_UTC_OFFSET` or the 08–18 window. Chat status facts reuse dashboard counts ([chat_facts.py, lines 145–157](../app/api/chat_facts.py#L145)). The AI explainer receives stored alert facts/evidence ([ai_explainer.py, lines 58–89](../app/adapters/model_providers/ai_explainer.py#L58)); it can therefore repeat an `after_hours` fact already computed by an alert, but does not independently calculate building time.
- Seed SQL inserts `Main Tower` and `Data Center Annex` without timezone or schedule values ([anzma_schema_and_seed.sql, lines 140–146](../db/init/anzma_schema_and_seed.sql#L140)). No customer timezone is established in the repository. ISS-008 records the fixed offset and hours, affected alert families, and the required golden-snapshot parity check ([SPRINT_1_TECHNICAL_ISSUES.md §ISS-008](SPRINT_1_TECHNICAL_ISSUES.md#L139)).

### Existing contract rules (not redefined here)

The Core computes freshness; connectors do not send `freshness` or `communication_status` ([INGESTION_CONTRACT.md §9](INGESTION_CONTRACT.md#L567)). It uses `last_observed_at` for data age and `last_received_at` for source liveness (§9). For expectation interval `E`, the contract defines `FRESH` at age `≤2E`, `STALE` at `2E < age ≤5E`, and `MISSING` above `5E`; `UNKNOWN` means no observation has ever been received (§9). Its proposed defaults are 900 seconds for telemetry, 3600 seconds for heartbeat, and source keepalive 300 seconds; source liveness becomes `STALE` after 2× and `LOST` after 5× (§9). Backfilled old observations do not make an asset fresh (§9). The contract also states in §1 and §5 that silence is not evidence of `OFFLINE` or `UNAVAILABLE` ([§1](INGESTION_CONTRACT.md#L14), [§5](INGESTION_CONTRACT.md#L484)).

## 2. Proposed model and additive migration

**Expectations.** Migration 009 creates `reporting_expectations` with exactly the contract §9 scopes: `ASSET_METRIC`, `ASSET_TYPE_METRIC`, `SOURCE`, `GLOBAL`. Scope keys are nullable and constrained so exactly the keys for that scope are present. It includes `record_kind` and metric/event type keys so heartbeat expectations can be represented, plus `expected_interval_seconds`, stale/missing multipliers, priority, validity bounds, expectation version, and audit timestamps. Resolve in contract order: asset+metric → asset-type+metric → source → global; priority selects among applicable overrides, with equal-priority ambiguity prohibited. There is no generic per-asset scope.

**Defaults:** global telemetry `E=900s`, heartbeat `E=3600s`, source keepalive `300s`, stale multiplier `2`, and missing multiplier `5`, matching contract §9. Migration 009 inserts these defaults. Store the source keepalive as a `GLOBAL` expectation with `record_kind=source`; allow `SOURCE` overrides for keepalive or reporting intervals. An unconfigured telemetry/heartbeat series uses the relevant global interval. Event-only assets without an expected heartbeat are `NOT_APPLICABLE`, consistent with §9. All freshness expectations are always on and use UTC elapsed age; building work hours do not pause or gate freshness. Work hours are used later for alert logic (ISS-008) only.

**Gaps:** Migration 009 creates `data_gaps` for a series that reaches `MISSING`; the future evaluator sets `gap_start=last_observed_at`, leaves `gap_end=NULL` while open, and records `opened_at`, `last_evaluated_at`, `closed_by`, and closure time. A stable unique key over series + `gap_start` makes opening idempotent. The future evaluator closes the gap at the first new accepted observation. Backfill-driven gap splitting/repair is a documented limitation and is not implemented in E2. Keep closed rows for audit. A rejected record is not an observation and cannot close a gap. A series with no prior observation remains `UNKNOWN` per contract §9; it is not silently relabeled `MISSING`.

**Current freshness:** Migration 009 creates `series_freshness_current`, with a unique key per observed series and fields for `freshness`, `last_observed_at`, `last_received_at`, `age_seconds`, `expected_interval_seconds`, `evaluated_at`, and expectation/version. E3 will populate it idempotently. At 1M+ facts, a precomputed current row is intended to avoid rescanning history for every dashboard read; performance still needs measurement. Keep this distinct from `asset_state_current`/availability and from raw connector `equipment_state_records`; integration into Core-derived equipment state belongs to item 1.4.

**Building schedule:** Migration 009 adds nullable IANA `buildings.timezone`, `work_start_local`, `work_end_local`, and `non_working_weekdays` (ISO weekday numbers 1–7); holidays/non-working dates are deferred. It backfills existing buildings to `Etc/GMT-2`, 08:00–18:00, and an empty non-working-weekday array. `Etc/GMT-2` represents fixed UTC+02 and preserves the current constant-offset behavior; it is a compatibility default, not a claim about the real site. New rows/NULL values remain valid. `freshness_admin.py` validates administrator-supplied schedules; future application consumers must interpret NULL using these same parity defaults and emit one warning. The actual customer timezone and weekend are unknown.

**Migration plan:** Migration 009 is additive and idempotent after 008; 006–008 are unchanged. It does not rewrite historical fact rows, drop objects, or fabricate ledger entries. Migration 006 creates `schema_migrations`; the runner takes an advisory lock, considers migrations numbered 006+, and skips ledgered versions ([apply_migrations.py, lines 24–57](../scripts/apply_migrations.py#L24)). With no ledger, its legacy branch applies additive 006 to create one, so an existing volume must first have its actual schema/ledger state checked and rehearsed. The only data backfill in 009 is existing-building timezone/schedule defaults; new expectation/gap/current tables start with no historical facts. Building columns remain nullable; making them `NOT NULL` belongs to item 1.6. `freshness_admin.py` accepts validated administrator-supplied values and emits audit lines. `building_code NOT NULL` remains outside this item.

## 3. Freshness evaluation job

**Inputs.** Evaluate accepted `telemetry_records` by `observed_at` for point freshness; use `event_records` only for explicitly configured heartbeat/event expectations; read `equipment_state_records` as explicit reported evidence without letting it overwrite derived health. Use `data_sources.last_received_at` for source liveness, `last_success_at`/error fields as diagnostics, and `ingestion_batches.delivery_mode` plus `connector_status` as connector context. Do not treat a `BACKFILL` batch's receipt time as a fresh observation. Rejections are audit evidence, not operational measurements.

**Schedule and rules.** Proposal: one idempotent pass every 60 seconds, using current UTC wall time (not the latest stored fact time used by the existing pipeline), with a persisted evaluation watermark and per-source work queue. For each due series, select its latest accepted observation and apply the exact §9 thresholds. `UNKNOWN` remains distinct from `MISSING`. Freshness is always evaluated; work hours and non-working weekdays do not pause or gate it. A heartbeat expectation is evaluated as a reporting series. Source liveness uses `last_received_at` against the keepalive threshold independently of point freshness.

On each pass, upsert the current series row and open/close gaps using a deterministic series/start identity. Reprocessing the same watermark and current observations must produce no duplicate rows. Open a gap when freshness first reaches `MISSING`, using `last_observed_at` as `gap_start`; close it at the first new observation. Backfill splitting is not implemented. This item generates no alerts and no incidents. The job does not synthesize `OFFLINE`, `UNAVAILABLE`, or an equipment fault from silence. It reports source `STALE`/`LOST` liveness separately. Those values must not be mapped to asset availability; Core-derived equipment health/state changes are item 1.4.

**Cost and failure behavior.** Process changed sources/series incrementally rather than scanning all history each minute; process records in bounded pages (including 5,000-record batches) and persist a cursor only after the page's writes commit. Existing candidate indexes include `telemetry_records(ts)`, `event_records(asset_id, ts)`, and migration-006 `(source_id, received_at)` indexes; use `EXPLAIN (ANALYZE, BUFFERS)` on representative disposable data before proposing any index. At ≥1M rows, the 1.5 performance check must measure lag, rows/pages read, lock time, and query plans; this note makes no runtime-cost claim. If a page fails, roll it back, retain the last committed cursor, log a bounded error, and retry with backoff. If the job is delayed or stopped, freshness is marked with its actual `evaluated_at`; do not advance source timestamps or pretend evaluation succeeded.

To avoid misleading restart/outage alarms, do not reset observation timestamps on Core startup and do not open a second gap for an already-open interval. Group simultaneous stale series by source before reporting. A connector outage can legitimately make source liveness stale; once a keepalive or live batch is received, source liveness recovers while old backfill remains stale by `observed_at`. A configured startup grace for notifications (not for calculations) is a human decision; no such grace exists today.

## 4. Source-level liveness and grouped reporting

Follow the contract's proposed source thresholds: `STALE` after 2× keepalive and `LOST` after 5× (§9); with 300-second keepalive this is 600 and 1,500 seconds. Proposal for correlated impact: classify a source-level condition when at least 3 expected series and at least 60% of that source's configured series are stale in one evaluation window. These are candidate thresholds requiring approval and calibration. Report the source code, last received time, age, affected/expected series counts, and observed connector diagnostics; restrict the detail to authorized operators.

For a correlated source issue, report one source-level condition plus affected series in the freshness read model. This does not suppress explicit fault/offline events or alter individual freshness rows. This grouping is diagnostic only: silence never creates `OFFLINE`/`UNAVAILABLE`, this item creates no alerts/incidents, and Core-derived health/state is item 1.4.

## 5. UTC, local time, DST, and parity

Store timestamps as UTC `TIMESTAMPTZ`; ingestion already normalizes timestamps to UTC ([ingestion.py, lines 80–97](../app/services/ingestion.py#L80)). Convert to building-local time only when evaluating a configured local schedule or formatting a building-local display, using the building's IANA zone. Keep API timestamps in UTC and, if needed, add a clearly named local timestamp/zone field. Do not apply a fixed `+2` to stored instants.

The seed has no timezone values and the customer's likely zone is unknown, so a customer-specific DST example cannot be verified from this repository. Under the compatibility default `Etc/GMT-2`, there is no DST transition. Building-local work-hour conversion will be used for later alert logic only; freshness calculations remain UTC and always on. A future alert-time test must cover spring-forward and fall-back behavior for the human-selected IANA zone.

For zero behavior change in the current demo, compare old alert output against the later building-schedule alert logic with `Etc/GMT-2`, 08:00–18:00, all seven days, and compare golden alert IDs, `after_hours` facts, regime labels, and evidence for the same UTC records. Include the seed's stored `details.after_hours` behavior for CCTV motion. Require zero differences for this parity configuration; separately test a selected DST zone. Do not alter KPI `no_data` or hourly heartbeat-compliance calculations as part of this work.

## 6. API, dashboard, and AI exposure (design only)

Proposal: add authenticated operator read endpoint `GET /api/v1/freshness` with optional `building_code`, `source_code`, `asset_code`, `metric_code`, and `state` filters. Return `{evaluated_at, timezone, source_liveness, items:[{asset_code, metric_code, freshness, last_observed_at, last_received_at, age_seconds, expected_interval_seconds, open_gap}]}`. Add `GET /api/v1/data-gaps` for paginated open/recent gaps and their closure evidence. Use the existing operator API authentication dependency (`require_api_auth` in [`security.py`](../app/security.py#L89)); do not accept ingestion source keys for these endpoints.

Keep existing dashboard/KPI/alert response fields and meanings unchanged. A later UI may consume these new endpoints or add optional `freshness` fields to equipment-health responses; deploy additively and retain the old `no_data` semantics until separately approved. AI/chat should use freshness only as a cited, timestamped Core fact from the new read model; it must not infer faults or replace stored alert evidence. Local time display must state its IANA zone while UTC remains the stored/API canonical value. These shapes and consumer choices remain proposals.

## 7. Test and performance plan

- Unit tests: injected clock for `UNKNOWN/FRESH/STALE/MISSING`; threshold boundaries at 2E/5E; backfill does not refresh point age; source 2×/5× liveness; idempotent gap open/close at `last_observed_at`; first-observation closure; schema/CLI validation for expectations and weekdays. Later alert logic tests cover DST transitions for the selected zone.
- Integration: run the evaluator against a disposable PostgreSQL database with representative source, telemetry, heartbeat, event, state, rejection, and batch rows; restart/replay the job and prove unchanged gap/current rows. No shared/live DB.
- Time parity: run the legacy and new alert calculations over the same demo fixture with fixed UTC+02 and 08–18/all-days; compare golden outputs and require zero mismatches before replacing the constants.
- 1.5 performance outline: synthetic ≥1M telemetry rows, multiple sources and expectations; benchmark 5,000-row ingestion followed by one evaluation cycle; record execution plans, buffers, wall time, queue lag, and lock duration at increasing source counts. Add indexes only when these measurements show a needed access path.
- Keep Phase-0 unit, data-integrity, day-2 integration, frontend/API (including 16 contract vectors), dashboard smoke, e2e, migration, and deploy-security suites green. Unit tests need no Core; database integration/performance require a disposable PostgreSQL; frontend/API and dashboard suites require an API/Core instance. Real deployment wiring is not part of E1.

## 8. Proposed work split

1. **E2 — additive schema, defaults, and provisioning** (≤10 files per implementation commit): migration 009 for building schedules, expectation/gap/current tables and constraints; validated admin CLI; schema and existing-volume migration verification on a disposable copy. Complete with this package.
2. **E3 — evaluator and job** (≤10 files): interval resolution, UTC/local schedule evaluation, incremental/idempotent gap and current-state writes, injected-clock unit and disposable-DB tests. Depends on E2.
3. **E4 — source correlation and read API** (≤10 files): source thresholds/grouping, authenticated freshness/gap endpoints, response tests and operator documentation. Depends on E3; does not change Core equipment state.
4. **E5 — consumers and rollout parity** (≤10 files): optional dashboard/chat freshness exposure and building-timezone replacement in existing alert-hour logic; freshness itself generates no alerts. Replace hardcoded alert time logic only after the zero-difference fixed-offset parity test, then run selected-zone DST and performance checks. Depends on E2–E4 and explicit approval of the timezone/schedule.

## 9. Decisions needed from the human

| Decision | Recommendation (proposal) |
|---|---|
| Defaults | Keep contract defaults: telemetry 900s, heartbeat 3600s, source keepalive 300s, stale 2×, missing 5×. |
| Expectation granularity | Use exactly contract §9 scopes: asset+metric, asset-type+metric, source, and global. |
| Job schedule | Every 60 seconds, bounded incremental pages, persisted watermark. |
| Source grouping threshold | Start with ≥3 expected series and ≥60% stale; calibrate against demo and site data. |
| Existing-building timezone/schedule | Backfill `Etc/GMT-2`, 08:00–18:00, no non-working weekdays. Keep fields nullable and make application NULL fallback log once; item 1.6 decides NOT NULL. |
| Gap lifecycle | Open on `MISSING` from `last_observed_at`; close on first new observation; no backfill split/repair in this item. |
| Gap storage | Persist open/closed `data_gaps` for audit; maintain a separate current-state row. |
| Admin configuration | Use the validated `freshness_admin.py` CLI with audit output; no admin API in E2. |
| Non-working days | Store ISO weekdays only; defer holiday dates/calendars. |
| Timezone examples | Provide the actual customer/site timezone; none is present in seed data. |

**Decision: APPROVED with amendments, 2026-11-20.** Remaining threshold/site choices below require configuration before the later rollout.

## Approved amendments

- **A1:** Expectation scopes are exactly `ASSET_METRIC`, `ASSET_TYPE_METRIC`, `SOURCE`, and `GLOBAL`, in contract §9 order; no generic asset scope.
- **A2:** Freshness is always on. There is no active-window column or work-hours freshness gating; building work hours are for later alert logic only.
- **A3:** A gap opens at `MISSING` with `gap_start=last_observed_at`, closes at the first new observation, and is not split/repaired for backfill here.
- **A4:** Freshness produces no alerts or incidents.
- **A5:** Store non-working weekdays only; defer holiday dates.
- **A6:** Building timezone/schedule columns stay nullable and existing buildings are backfilled. Future app behavior uses `Etc/GMT-2`, 08:00–18:00, no non-working days for NULL and logs one warning. `building_code NOT NULL` is item 1.6.

## 10. Limitations to carry into the Phase 1 list

- No freshness evaluator, expectation configuration, persisted gap history, or current-freshness read model exists today.
- `data_sources` timestamps describe batch receipt/success/error, not per-asset reporting cadence; `connector_status` is opaque JSON diagnostics.
- Existing dashboard `no_data` and hourly heartbeat compliance remain separate legacy KPIs until a reviewed parity/consumer change.
- Fixed UTC+2 ignores DST and per-building differences; seed buildings and customer site timezone/schedule are not configured in the schema.
- Source-level grouping thresholds, the customer timezone/weekend, holiday handling, and site calibration remain open.
- No ≥1M-row performance result is claimed; query plans and measured indexes are required before adding indexes.
