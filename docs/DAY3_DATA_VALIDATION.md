# Day 3 Data Validation Report

**Product:** Anzma AI Insight Core v0.1  
**Validation date:** 18 November 2026  
**Validated code commit:** `1ae9eef54dc03d5a20cf9dfed23752731cae20c1`  
**Branch:** `codex/fix-bandwidth-utilization`  
**Validation owner:** Ahmed  
**Assigned reviewer:** Amr (review not recorded)  
**Engineering approval:** Eng. Mohsen, Engineering Lead — Ahmed confirmed approval of the bandwidth interpretation and thresholds on 18 November 2026.

> This report is a data-validation record. It is not the final Day 3 acceptance or release sign-off.

## 1. Executive Summary

The data and regression checks described below passed against the code in commit `1ae9eef54dc03d5a20cf9dfed23752731cae20c1`. `bandwidth_util` is stored as a percentage, and the alert rules, evidence facts, summaries, anomaly labels, integrity catalogue, and packet-loss playbook comparison now use percent consistently.

Eng. Mohsen approved the current operating limits: 80% warning, 90% critical mean, and a 15 percentage-point minimum anomaly deviation. The packet-loss cause split is a heuristic that selects a possible explanation from bandwidth evidence. It is not a confirmed diagnosis.

Five counted automated regression suites passed **138/138 checks**. The dashboard API script exited successfully, the separate S1 fallback and live-model runs passed, and the schema verification reported `OVERALL OK`. Those results do not establish visual UI coverage or final Day 3 acceptance.

## 2. Approval Record and Threshold Interpretation

The canonical database unit for `bandwidth_util` is `%`. No per-link capacity field is stored, so the current telemetry cannot be converted to Mbps. The approved operating limits are:

| Rule | Approved limit | Implemented behavior |
|---|---:|---|
| Warning utilization | 80% | Warning after three consecutive readings strictly above 80%. |
| Critical utilization | 90% mean | Critical when the mean of a qualifying run is at least 90%. |
| Bandwidth anomaly | 15 percentage points | Minimum absolute deviation from the time-of-day baseline; the robust z-score and three-reading persistence requirements also apply. |
| Packet-loss cause split | 80% mean utilization | Above 80% selects a congestion hypothesis; 80% or lower selects a link-quality/physical-layer hypothesis. Neither outcome proves the cause. |

**Approval evidence:** Ahmed confirmed that Eng. Mohsen approved the canonical unit and all three thresholds on 18 November 2026. No written email, ticket, meeting record, or other approval reference was available during this review. Attach the written approval reference when available; none is invented in this report.

The thresholds are approved operating limits for the current model, not capacity-derived limits for each individual link. Reassess them if link capacity/configuration data becomes available or operating requirements differ by link.

## 3. Git and Docker Traceability

### Git state

- `git rev-parse HEAD`: `1ae9eef54dc03d5a20cf9dfed23752731cae20c1`
- Commit: `1ae9eef54dc03d5a20cf9dfed23752731cae20c1` — `Fix bandwidth utilization units and thresholds`
- Branch: `codex/fix-bandwidth-utilization`
- `git status --short` at final review:

```text
?? docs/CONNECTOR_ARCHITECTURE.md
?? docs/CONTRACT_TO_DB_MAPPING.md
?? docs/DAY3_DATA_VALIDATION.md
?? docs/INGESTION_CONTRACT.md
```

There were no tracked-file modifications in that status output. The report and the three other listed documents were untracked at review time; they are not part of commit `1ae9eef`.

### Docker image and source comparison

- API image ID returned by `docker compose ... images -q api`: `sha256:68beebfcac660d56dbff01d77814a0ef181448d031c6b214f7c984e033e9ba9d`.
- The image was built from the working tree used for validation immediately before the code was committed. The code files tested were then committed as `1ae9eef`; no code changes were made between validation and that commit.
- SHA-256 comparisons between the files in the running API container and the corresponding files in `HEAD` matched:

| File | SHA-256 in API container and `HEAD` |
|---|---|
| `app/domain/alerts/alert_config.py` | `ed4e0aa5a2aefc204950b257e7ad041605f9de9d5a3b35da8c0eb468d8b616f4` |
| `app/domain/alerts/alert_engine.py` | `82335efaac0c4051dab632206e1f5ebeb321ff9d3687f7a76ef6dd8c49d91321` |
| `app/domain/operations/integrity_rules.py` | `10f58112fa2b5931170bc4b617ee8528222a4df67952f59e90548c218260b892` |
| `tests/unit/test_day2_ahmed.py` | `01e644fe211dcca838bd24291767c675d80547e8af5f203deb60bb8247d9ac7b` |

**Provenance limitation:** the Docker image has no recorded Git revision label or retained build attestation, and the build command output was not saved as a file. The image ID and matching hashes prove that the changed runtime files and bandwidth regression test match `HEAD`; they do not prove byte-for-byte identity of every file in the image or provide a full build-provenance attestation.

## 4. Environment and Validation Categories

- **Environment:** isolated Docker Compose project `anzma-d3-verify`, PostgreSQL 16, API rebuilt from the validated working tree, and an Ollama endpoint available to the API for the live S1 run.
- **Seed/test data:** database-backed tests used throwaway databases. The Day 2 integration test used its seeded dataset. The primary application stack was not used for these checks.
- **Shared QA state:** the isolated `anzma-d3-verify` dashboard database had incident #6 (HVAC) manually acknowledged and resolved during a prior browser exercise. The dashboard snapshot below reflects that changed state. Fresh-database integration results are reported separately.

The evidence is separated by verification type:

| Verification type | What was checked | Status and limit |
|---|---|---|
| API-level verification | Backend rules, database integrity, evidence references, lifecycle endpoints, dashboard API data, and chat/API consistency. | Automated checks passed as listed in §5. |
| Automated UI/API tests | `test_frontend_api.py` uses FastAPI `TestClient` against the dashboard-facing endpoints and checks static page/route responses. `test_dashboard.py` checks dashboard API output. | These are API/route tests; they do not render or visually inspect the page in a browser. No automated browser UI test suite was run. |
| Actual browser interaction | The dashboard was opened in the isolated browser; incident #6 was opened, acknowledged, and resolved, and the returned accessibility state showed the evidence count and refreshed incident/alarm state. | Partial manual browser check only. No screenshot or continuous video artifact was retained. Filters, assistant interaction, and visual UNKNOWN/NO DATA presentation are not established by this report. |

UNKNOWN and NO DATA were verified at API/regression level only. They must not be described as visually UI-verified until the browser walkthrough records and inspects those states.

## 5. Automated Results and Log Paths

The counted total below is exactly the sum of the five listed suites: `28 + 43 + 18 + 31 + 18 = 138`. Dashboard, S1, and schema checks are reported separately and are not included in 138/138.

| Suite / check | Result | Actual log path | Notes |
|---|---:|---|---|
| `tests/unit/test_day2_ahmed.py` | **28/28 PASS** | `/tmp/d3_unit_ahmed_final.txt` | Includes unit checks, percentage threshold cases, anomaly units, and packet-loss playbook selection. |
| `tests/integration/test_day2_integration.py` | **43/43 PASS** | `/tmp/d3_day2_integration_final.txt` | Recreated `anzma_day2_test`; fresh detection yielded 10 alerts and 8 incidents; rerun yielded `new_alerts=0`. |
| `tests/integration/test_data_integrity.py` | **18/18 PASS** | `/tmp/d3_data_integrity_final.txt` | Recreated `anzma_integrity_test`; schema/integrity and alert uniqueness checks. |
| `tests/integration/test_chat_consistency.py` | **31/31 PASS** | `/tmp/d3_chat_final.txt` | Ran against the isolated running application data; checks chat/dashboard count and status agreement. |
| `tests/integration/test_frontend_api.py` | **18/18 PASS** | `/tmp/d3_frontend_final.txt` | Recreated `anzma_api_test` internally; endpoint, evidence, lifecycle, chat, and readiness checks. |
| **Counted automated suite total** | **138/138 PASS** | Logs listed above | Only the five suites in this table are included in this total. |
| `tests/integration/test_dashboard.py` | **PASS — command exit 0** | `/tmp/d3_dashboard_final.txt` | Prints dashboard API KPI output; it does not report a numeric check count. Snapshot reflects the isolated QA incident #6 resolution described in §4. |
| `tests/scenarios/e2e_s1.py --build` | **14/14 PASS** | `/tmp/d3_e2e_fallback_final.txt` | Deterministic fallback; `ai_source=fallback`, no model attempt, 14/14 incident evidence rows and 7/7 explanation rows verified. |
| `tests/scenarios/e2e_s1.py --build --llm` | **15/15 PASS** | `/tmp/d3_e2e_live_final.txt` | Live AI; `ai_source=llm`, `model_used=qwen2.5:7b`, one model attempt, 14/14 incident evidence rows and 7/7 explanation rows verified. |
| `scripts/verify_schema.sql` | **OVERALL OK** | `/tmp/d3_schema_after_bw.txt` | All reported schema, index, key, uniqueness, check-constraint, and foreign-key checks were OK. |

The S1 scripts ran inside the container, where Git metadata was unavailable; their outputs therefore say `git not available`. The host checkout/image traceability is recorded in §3. Both S1 explanations returned `grounding_supported`; this records grounding checks and does not guarantee semantic correctness of every comparison.

The listed `/tmp` logs are actual files on the validation host, not repository artifacts. Copy them into the independent validation package if the reviewer must inspect them on another machine.

## 6. Bandwidth Data and Expected Alert Impact

The freshly seeded `anzma_day2_test` database contained 626 `bandwidth_util` telemetry rows:

| Canonical unit | Rows | Minimum | Maximum | Mean |
|---|---:|---:|---:|---:|
| `%` | 626 | 15.55 | 72.02 | 42.75 |

The fresh detector run produced 10 alerts and 8 incidents. No `NET_BANDWIDTH_HIGH` or `NET_BANDWIDTH_ANOMALY` alert was generated from this seed. Maximum utilization was 72.02%, below the 80% warning threshold. The packet-loss alert had mean bandwidth utilization of 68.6%, so the playbook selected the link-quality/physical-layer hypothesis.

Synthetic regression cases verified the configured behavior:

- Three readings at 82%, 85%, and 88% produced a WARNING with a mean of 85%.
- Three readings at 91%, 93%, and 95% produced a CRITICAL alert with a mean above 90%.
- Readings below 80% produced no high-utilization alert.
- Packet loss with 85% mean utilization selected the congestion hypothesis/action.
- Packet loss with 65% mean utilization selected the link-quality/physical-layer hypothesis/action.
- Bandwidth anomaly facts and summaries used `%`, not Mbps.

**Cause-split interpretation:** the bandwidth comparison is a supporting heuristic only. Mean utilization above 80% plus packet loss supports investigating congestion; utilization at or below 80% supports investigating link quality/physical-layer causes. Neither branch proves the cause or rules out the alternative. Confirm with independent evidence such as queue/drop counters, CRC/error counters, transceiver/cable inspection, and other available link diagnostics.

The current seed remained at 10 alerts and 8 incidents; the approved limits did not add a high-utilization alert to the seeded data. Future measurements above the limits may create alerts and change packet-loss cause/action suggestions.

## 7. Operational Integrity Findings

| Check | Result | Evidence / interpretation |
|---|---|---|
| Measurement units | **PASS at API/rule level** | Database metric, alert thresholds/facts/summaries, anomaly labels, integrity catalogue, and playbook comparison use `%`. Integrity check accepts `%` and rejects `Mbps` for `BANDWIDTH_USAGE`. |
| Events, alerts, and incidents | **PASS at integration/API level** | Fresh detection reported separate source events, alert rows, and grouped incidents; 10 alerts and 8 incidents. |
| Evidence traceability | **PASS at database/API level** | S1 incident evidence: 14/14 source rows verified; each explanation: 7/7 rows verified. Tampered evidence checks passed in integration tests. |
| Deduplication | **PASS at integration/database level** | Second detection returned `new_alerts=0`; alert/incident totals were unchanged. Database uniqueness checks passed. |
| Dashboard/chat consistency | **PASS at API level** | Chat/dashboard consistency suite 31/31; frontend API suite 18/18; dashboard script exited 0. |
| UNKNOWN / NO DATA | **PASS at API/regression level; UI not visually verified** | Tests verified never-reported assets as UNKNOWN and stale assets as WARNING. No saved visual evidence establishes how these states render in the browser. |
| AI grounding | **PASS with semantic limitation** | S1 sources/model identifiers and evidence checks matched the expected paths. Grounding validation is not a semantic-accuracy guarantee. |

## 8. Known Limitations and Outstanding Items

1. **Per-link capacity:** no capacity/configuration value is stored. The approved percentage thresholds are not individualized per link.
2. **Cause split:** the packet-loss classification is a hypothesis selector, not a diagnosis. Operators need independent link evidence before treating a cause as established.
3. **AI semantic validation:** the validator checks grounding and allowed values but does not fully validate every semantic comparison. A prior network explanation misdescribed a measured loss value as a threshold. The live/fallback S1 runs here used HVAC; a network-alert explanation was not rerun after this change.
4. **Playbook approval metadata:** recommendations still contain placeholder metadata (`Ahmed (Day 2 playbook owner)`). Eng. Mohsen's approval recorded here covers the bandwidth unit and thresholds only; it does not authorize individual playbook actions. Do not present the placeholder as formal approval.
5. **Browser evidence:** browser interaction was performed, but no screenshot or recording was retained. Full visual checks for filters, assistant references, UNKNOWN/NO DATA, and recovered/history states remain outstanding.
6. **Standalone installation:** this report does not claim a clean full installation including Ollama model provisioning.
7. **Approval artifact:** Ahmed confirmed Eng. Mohsen's approval, but a written approval reference was unavailable for attachment. This remains an evidence-collection item for independent review.

## 9. Reproduction Commands

Run from the repository root using the isolated verification project and a disposable PostgreSQL test cluster. Set `.env` according to the repository's environment example; the configured database role must be allowed to create and drop the named test databases. Do not target the primary application database. Commands below are ordered so checks against the running QA state occur before tests that recreate named databases; `test_frontend_api.py` is last for clarity.

```bash
docker compose --env-file .env -p anzma-d3-verify \
  -f deploy/compose/docker-compose.yml \
  -f test_logs/verification/docker-compose.override.yml \
  up -d --build api

docker compose --env-file .env -p anzma-d3-verify \
  -f deploy/compose/docker-compose.yml \
  -f test_logs/verification/docker-compose.override.yml \
  exec -T api python tests/unit/test_day2_ahmed.py

docker compose --env-file .env -p anzma-d3-verify \
  -f deploy/compose/docker-compose.yml \
  -f test_logs/verification/docker-compose.override.yml \
  exec -T api python tests/integration/test_day2_integration.py

docker compose --env-file .env -p anzma-d3-verify \
  -f deploy/compose/docker-compose.yml \
  -f test_logs/verification/docker-compose.override.yml \
  exec -T api python tests/integration/test_data_integrity.py

docker compose --env-file .env -p anzma-d3-verify \
  -f deploy/compose/docker-compose.yml \
  -f test_logs/verification/docker-compose.override.yml \
  exec -T api python tests/integration/test_chat_consistency.py

docker compose --env-file .env -p anzma-d3-verify \
  -f deploy/compose/docker-compose.yml \
  -f test_logs/verification/docker-compose.override.yml \
  exec -T -e PYTHONPATH=/app api python tests/integration/test_dashboard.py

docker compose --env-file .env -p anzma-d3-verify \
  -f deploy/compose/docker-compose.yml \
  -f test_logs/verification/docker-compose.override.yml \
  exec -T api python tests/scenarios/e2e_s1.py --build

docker compose --env-file .env -p anzma-d3-verify \
  -f deploy/compose/docker-compose.yml \
  -f test_logs/verification/docker-compose.override.yml \
  exec -T api python tests/scenarios/e2e_s1.py --build --llm

docker compose --env-file .env -p anzma-d3-verify \
  -f deploy/compose/docker-compose.yml \
  -f test_logs/verification/docker-compose.override.yml \
  exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1' \
  < scripts/verify_schema.sql

docker compose --env-file .env -p anzma-d3-verify \
  -f deploy/compose/docker-compose.yml \
  -f test_logs/verification/docker-compose.override.yml \
  exec -T api python tests/integration/test_frontend_api.py
```

### Database effects of test commands

- `test_day2_integration.py` drops/recreates `anzma_day2_test`.
- `test_data_integrity.py` drops/recreates `anzma_integrity_test`.
- Each S1 command with `--build` drops/recreates `anzma_e2e`.
- `test_frontend_api.py` drops/recreates `anzma_api_test`, seeds it, and runs its endpoint checks against that database. It does **not** depend on the default QA database being empty or on another suite running first. It is listed last for clarity. It requires a role allowed to create/drop databases; only run it against the isolated test PostgreSQL cluster.
- `test_chat_consistency.py` and `test_dashboard.py` use the running application/QA database state; their observations may vary if that database has been changed.
- `verify_schema.sql` is a read-only verification query against the configured application database.

The `--llm` S1 command requires the configured Ollama endpoint and Qwen model. The command without `--llm` verifies the deterministic fallback separately.

## 11. Ingestion data-path addendum — 19 November 2026

**This addendum supplements the historical 18 November validation above. Sections 1–10, their counts, and their commit-specific conclusions are retained unchanged.** The current ingestion evidence is from code commit `a9de778cf0793a530ae6e347480f07820c17ab7d` and disposable PostgreSQL 16 databases only; the shared/live database was not migrated.

### Legacy quality and provenance

The restored H0 database held 4,669 telemetry rows before migrations. After migrations 006–008, counts were unchanged, all 4,669 legacy rows still had `quality IS NULL`, and all 4,669 remained visible through `v_telemetry_valid`. All 955 legacy events remained source-less. This preserves historical facts and provenance uncertainty. Operational readers exclude `BAD`; legacy NULL quality is treated as usable/GOOD, but no GOOD value was written back to those rows.

### Accepted, duplicate, conflicting, and rejected records

The authenticated batch route was exercised against the migrated disposable H0 database and a clean Compose install:

- **Accepted:** a valid telemetry record returned HTTP `200`, `status: ACCEPTED`, `transaction: COMMITTED`, and one accepted record ID. A clean-install record stored value `22.5 C`; the H0 immutability case stored `20.0` with `quality=GOOD`.
- **Exact retry:** replaying the same body with the same `batch_id` returned the stored JSON result and `Idempotent-Replay: true`.
- **Exact fact duplicate:** the same natural key and content in a new batch returned `DUPLICATE`; it did not add a second measurement.
- **Conflicting fact:** the same `(asset_id, metric_id, ts)` with a different value returned HTTP `422`, `reason_code=CONFLICT`, and message “A different value is already stored for this asset, metric, and observed_at; first write was preserved.” The original stored value remained `20.0` and `quality=GOOD`.
- **Partial batch:** one valid and one unit-mismatched record returned HTTP `200`, `status=PARTIAL`, `transaction=COMMITTED`, counts `received=2`, `accepted=1`, `rejected=1`. The rejected record was kept in `ingestion_rejections` and was not inserted as a telemetry fact.
- **Unmapped asset:** vector 10 returned `ASSET_UNRESOLVED`, retained `source_id`, `external_id`, and payload in rejection audit, and appeared in `v_unmapped_external_assets`.
- **Plausibility warning:** configured minimum/maximum bounds returned accepted records with `OUT_OF_PLAUSIBLE_RANGE` warnings and stored quality `BAD`; those rows were excluded from `v_telemetry_valid`.
- **All rejected:** the 101-rejection case returned HTTP `422`, capped the inline response at 100 and marked `rejected_truncated=true`; authenticated GET returned all 101 audit rows with their payloads.

Rejections are audit/control-plane evidence. They are not accepted telemetry/event/state facts and do not become operational truth. These behaviors are covered by the separately named §13.3 vector tests and the Step 0.4 integration check in [`DAY3_FUNCTIONAL_TEST_REPORT.md`](DAY3_FUNCTIONAL_TEST_REPORT.md).

### Scope and limits

The new data-path examples used controlled synthetic/manual requests; no physical device or vendor connector was connected. The H0 data comparison found one last-digit floating-point difference in `avg_zone_temp`; other dashboard fields matched, and alert/incidents responses were byte-identical. No time fields were ignored. Details and exact hashes are in [`DAY3_MIGRATION_REHEARSAL.md`](DAY3_MIGRATION_REHEARSAL.md).

## 10. Review Status

**Data and API checks in scope:** PASS as recorded above.  
**Bandwidth interpretation and thresholds:** approved by Eng. Mohsen on 18 November 2026, as confirmed by Ahmed; written approval reference not available in this review.  
**Actual browser visual verification:** PARTIAL; no retained screenshot or recording, and UNKNOWN/NO DATA rendering is not visually verified.  
**Day 3 acceptance:** NOT DECLARED. Outstanding browser recording, clean standalone installation evidence, Amr's review, written approval reference, and playbook authorization metadata remain outside this report's completed checks.
