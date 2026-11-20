# Freshness, source health, gaps, and building schedules — design note

**Phase 1 item 1.3. Decision: APPROVED with amendments A1–A7; E2, E3, and E4a implemented, 2026-11-20.** The approved rules are recorded in [FRESHNESS_GAPS_ADDENDUM.md](FRESHNESS_GAPS_ADDENDUM.md). Freshness remains always on, gaps open only at `MISSING`, and a gap's boundaries are immutable evidence; gaps never change equipment state. A7 says `NOT_APPLICABLE` is computed but not persisted: no resolved series row means not applicable, for E4 to derive on read. `buildings.building_code NOT NULL` remains item 1.6.

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

**Expectations.** Migration 009 creates `reporting_expectations` with exactly the contract §9 scopes: `ASSET_METRIC`, `ASSET_TYPE_METRIC`, `SOURCE`, `GLOBAL`. Scope keys are nullable and constrained so exactly the keys for that scope are present. It includes `record_kind` and metric/event type keys so heartbeat expectations can be represented, plus `expected_interval_seconds`, stale/missing multipliers, priority, validity bounds, expectation version, and audit timestamps. Resolve in contract order: asset+metric → asset-type+metric → source → global; within one scope the larger numeric priority wins, then the most recent `valid_from`; equal-priority ambiguity for the same keys is prohibited. Validity uses `valid_from <= now < valid_to`. There is no generic per-asset scope.

**Defaults:** global telemetry `E=900s`, heartbeat `E=3600s`, source keepalive `300s`, stale multiplier `2`, and missing multiplier `5`, matching contract §9. Migration 009 inserts these defaults. Store the source keepalive as a `GLOBAL` expectation with `record_kind=source`; allow `SOURCE` overrides for keepalive or reporting intervals. An unconfigured telemetry/heartbeat series uses the relevant global interval. Event-only assets without an expected heartbeat are `NOT_APPLICABLE`, consistent with §9. All freshness expectations are always on and use UTC elapsed age; building work hours do not pause or gate freshness. Work hours are used later for alert logic (ISS-008) only.

**Gaps:** Migration 009 introduced the gap table; additive migration 010 adds `building_id`, detection-time expected interval, `detected_at`, informational `opened_in_work_hours`, and cause metadata. E4a opens one gap per series only at `MISSING`, snapshots `gap_start=last_observed_at`, and writes cause `UNKNOWN`. It does not gate detection by work hours; the flag uses the building's IANA zone, hours, and non-working weekdays. Retired/disabled assets and disabled sources do not open gaps. A gap closes only on a valid observation newer than both the opening `gap_start` and `detected_at`; a back-dated observation inside an open or closed gap does not close or split that history, and `BAD` telemetry is excluded by `v_telemetry_valid`. Cause may be updated only while open; E4b owns source classification (`NO_DATA`/`SOURCE_SUSPECTED`). Cause is evidence only. Database constraints enforce one open gap per series and freeze boundaries and closed causes. `UNKNOWN` series without a prior observation do not create gaps. No gap causes `OFFLINE` or changes asset health.

**Current freshness:** Migration 009 creates `series_freshness_current`, with a unique key per observed series and fields for `freshness`, `last_observed_at`, `last_received_at`, `age_seconds`, `expected_interval_seconds`, `evaluated_at`, and expectation/version. E3 populates it idempotently. At 1M+ facts, a precomputed current row is intended to avoid rescanning history for every dashboard read; performance still needs measurement. Keep this distinct from `asset_state_current`/availability and from raw connector `equipment_state_records`; integration into Core-derived equipment state belongs to item 1.4.

**Building schedule:** Migration 009 adds nullable IANA `buildings.timezone`, `work_start_local`, `work_end_local`, and `non_working_weekdays` (ISO weekday numbers 1–7); holidays/non-working dates are deferred. It backfills existing buildings to `Etc/GMT-2`, 08:00–18:00, and an empty non-working-weekday array. `Etc/GMT-2` represents fixed UTC+02 and preserves the current constant-offset behavior; it is a compatibility default, not a claim about the real site. New rows/NULL values remain valid. `freshness_admin.py` validates administrator-supplied schedules; future application consumers must interpret NULL using these same parity defaults and emit one warning. The actual customer timezone and weekend are unknown.

**Migration plan:** Migration 009 is additive and idempotent after 008; 006–008 are unchanged. It does not rewrite historical fact rows, drop objects, or fabricate ledger entries. Migration 006 creates `schema_migrations`; the runner takes an advisory lock, considers migrations numbered 006+, and skips ledgered versions ([apply_migrations.py, lines 24–57](../scripts/apply_migrations.py#L24)). With no ledger, its legacy branch applies additive 006 to create one, so an existing volume must first have its actual schema/ledger state checked and rehearsed. The only data backfill in 009 is existing-building timezone/schedule defaults; new expectation/gap/current tables start with no historical facts. Building columns remain nullable; making them `NOT NULL` belongs to item 1.6. `freshness_admin.py` accepts validated administrator-supplied values and emits audit lines. `building_code NOT NULL` remains outside this item.

## 3. Freshness evaluation job

**Inputs.** The broader design uses accepted `telemetry_records` by `observed_at` for point freshness and `event_records` for configured heartbeat/event expectations. E3 implements those series only; it does not evaluate source liveness, `equipment_state_records`, connector diagnostics, or rejections. `data_sources.last_received_at` and `ingestion_batches.delivery_mode` remain inputs for later source-level work. A `BACKFILL` receipt time is not a fresh observation.

**E3 implementation.** `app/services/freshness.py` provides the evaluator and database pass; `scripts/freshness_job.py` runs one pass or repeats it at a configurable interval (default 60 seconds). Each pass uses a UTC clock and re-evaluates every resolved series in deterministic keyset pages. Pages commit independently; a failed page rolls back while earlier pages remain committed. A restart begins at the first page, and idempotent writes make a repeated pass safe. No timestamp is reset at startup. E3 evaluates telemetry and explicitly expected event/heartbeat series; source-level liveness/correlation is not part of this series job. `UNKNOWN` remains distinct from `MISSING`. Freshness is always evaluated; work hours and non-working weekdays do not pause or gate it.

On each pass, upsert the current series row and open/close gaps using the unique series/start identity. Repeating the same pass with the same clock and inputs changes no stored values and creates no duplicate rows. A gap opens when freshness reaches `MISSING`, with `gap_start=last_observed_at`; it closes only when `MAX(observed_at)` is newer than the open gap start. An older late/backfilled observation does not close it. Gap splitting/repair is not implemented. `NOT_APPLICABLE` is returned by the pure evaluator but not written; candidates without an active expectation have no current row and no new gap. This job generates no alerts or incidents and does not change asset state, KPIs, dashboard, chat, or APIs. Silence never synthesizes `OFFLINE`, `UNAVAILABLE`, or an equipment fault.

**Cost and failure behavior.** E3 keyset-pages the resolved series universe and queries the latest accepted observation per series using `MAX(ts)`; it does not persist a cursor or claim incremental full-history cost. Telemetry reads `v_telemetry_valid`: `BAD` is excluded, while legacy `NULL` and `UNCERTAIN` quality remain included as that view specifies. `received_at` is stored as a diagnostic but never determines age, so a late-arriving `BACKFILL` row cannot make older observations fresher. Existing candidate indexes include `telemetry_records(ts)`, the telemetry natural key, `event_records(asset_id, ts)`, and migration-006 source/received indexes; use `EXPLAIN (ANALYZE, BUFFERS)` on representative disposable data before proposing an index. At ≥1M rows, item 1.5 must measure rows/pages read, lock time, lag, and query plans; no runtime-cost claim is made here. A failed page rolls back; prior committed pages remain. The job emits one bounded error line and exits non-zero; an operator may rerun it. A delayed/stopped job does not advance timestamps or pretend evaluation succeeded.

To avoid misleading restart/outage alarms, do not reset observation timestamps on Core startup and do not open a second gap for an already-open interval. Group simultaneous stale series by source before reporting. A connector outage can legitimately make source liveness stale; once a keepalive or live batch is received, source liveness recovers while old backfill remains stale by `observed_at`. A configured startup grace for notifications (not for calculations) is a human decision; no such grace exists today.

### E3 operator run

Set explicit database connection environment variables for the intended database; `PGHOST` and `PGPORT` are required and the job has no host/port defaults. For example, after loading credentials from the operator's protected environment file:

```sh
export PGHOST=127.0.0.1
export PGPORT=25443
export PGDATABASE=anzma
# PGUSER and PGPASSWORD come from the protected deployment environment.
python3 scripts/freshness_job.py --once
```

For repeated local evaluation, run `python3 scripts/freshness_job.py --loop --interval 60`. The JSON line reports evaluated series, their state counts, gaps opened/closed, page commits, and duration. No Compose service or scheduler is installed by E3; production scheduling/wiring is a later decision. Read the current persisted values directly for now:

```sql
SELECT freshness, count(*) FROM series_freshness_current GROUP BY freshness ORDER BY freshness;
SELECT asset_id, source_id, record_kind, metric_code, event_type, gap_start, last_evaluated_at
FROM data_gaps WHERE gap_end IS NULL ORDER BY gap_start;
```

`NOT_APPLICABLE` is not stored under amendment A7: an event-only asset without an expected heartbeat has no current row, and E4 will derive that state on read. E3 exposes no HTTP endpoint.

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
| Job schedule | Every 60 seconds, re-evaluate all resolved series in bounded pages. E3 keeps the keyset cursor in memory and restarts at the first page; revisit persistence only if operations/performance require it. |
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
- **A7:** Do not persist `NOT_APPLICABLE`; absence of a resolved series row means not applicable, to be derived by E4 on read. Do not alter the E2 schema.

## 10. Limitations to carry into the Phase 1 list

- E3's evaluator/job is available through the CLI only; Compose scheduling, E4 read APIs, and source-level liveness/correlation remain unimplemented.
- `NOT_APPLICABLE` has no persisted row; E4 must derive it from absence. Gap splitting/repair after backfill is not implemented.
- No ≥1M-row performance result is claimed; query plans and measured indexes are required before adding indexes.
- `data_sources` timestamps describe batch receipt/success/error, not per-asset reporting cadence; `connector_status` is opaque JSON diagnostics.
- Existing dashboard `no_data` and hourly heartbeat compliance remain separate legacy KPIs until a reviewed parity/consumer change.
- Fixed UTC+2 ignores DST and per-building differences; seed buildings and customer site timezone/schedule are not configured in the schema.
- Source-level grouping thresholds, the customer timezone/weekend, holiday handling, and site calibration remain open.
