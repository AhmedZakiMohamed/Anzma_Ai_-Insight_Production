# Day 3 migration rehearsal — Phase 0 item 0.5

**Run date:** 2026-11-19 (UTC)  
**Repository commit at rehearsal:** `c12a2c6f501f14d594d386d4de5c9f1c0463e4a1`  
**Database:** private disposable PostgreSQL 16 container, Compose project `anzma-h0-rehearsal`; no shared/live database was contacted.  
**Scope:** rehearsal, compatibility checks, regression suites, and this report only. No push or tag was made.

## 0.5 preflight evidence

### Standalone command check

`scripts/apply_migrations.py` and `scripts/ingestion_admin.py` both add the repository root to `sys.path` before importing `app`. The migration runner previously attempted to connect to PostgreSQL even for `--help`; it did not fail with `ModuleNotFoundError`. Commit `c12a2c6` added `argparse` help handling, committed separately as requested.

Command and output from the repository root:

```text
$ python3 scripts/apply_migrations.py --help
usage: apply_migrations.py [-h]

Apply unapplied additive database migrations (currently 006+). Usage: python
scripts/apply_migrations.py Reads PostgreSQL connection settings from
PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD.

options:
  -h, --help  show this help message and exit
```

Command and output from `/private/tmp`:

```text
$ cd /private/tmp && python3 /Users/test/Desktop/Anzma_Ai_Insight_Production/scripts/apply_migrations.py --help
usage: apply_migrations.py [-h]

Apply unapplied additive database migrations (currently 006+). Usage: python
scripts/apply_migrations.py Reads PostgreSQL connection settings from
PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD.

options:
  -h, --help  show this help message and exit
```

`ingestion_admin.py --help` also ran from `/private/tmp` using its absolute path; the API container additionally ran the `register-building`, `create-source`, and `map-asset` subcommand help. The standalone command output was:

```text
+$ cd /private/tmp && python3 /Users/test/Desktop/Anzma_Ai_Insight_Production/scripts/ingestion_admin.py --help
+usage: ingestion_admin.py [-h]
+                          {create-source,register-building,rotate-key,revoke-key,map-asset,set-backdate-limit}
+                          ...
+
+Provision Core ingestion sources, rotate/revoke keys, and map source-local
+asset IDs. The generated API key is printed once by create-source/rotate-key.
+Store it in a secret manager.
+
+positional arguments:
+  {create-source,register-building,rotate-key,revoke-key,map-asset,set-backdate-limit}
+    create-source       register a source and issue its first key
+    register-building   assign an external-safe code to an existing building
+    rotate-key          issue a second active key; revoke the old key
+                        separately
+    revoke-key          revoke a credential immediately by source and prefix
+    map-asset           add a source-scoped external asset mapping
+    set-backdate-limit  set or clear a source's maximum accepted back-date
+
+options:
+  -h, --help            show this help message and exit
+```
+
+No plaintext key is included in this report.

### CRAC regression change

The 19/20 to 20/20 change was a **test-only** change in commit `397797b`; no production code changed in that commit. The test assertion was not simply removed or weakened. It stopped assuming the seeded CRAC had no open critical alerts (another test can create them) and used an isolated HVAC fixture to test recovered-history semantics, then asserted the seeded CRAC is critical exactly when it has an active critical alert.

Full diff of the CRAC-related test change:

```diff
@@ -214,7 +214,7 @@
-@test("GET /dashboard/equipment-health: one row per asset with the fields the table reads; critical first")
+@test("GET /dashboard/equipment-health: recovered history stays visible without an active critical; active alerts stay critical")
 def _():
     d = client.get("/dashboard/equipment-health", params={"hours": 72}).json()
     assert len(d["items"]) == 12 and d["window"]["to"]
@@ -223,9 +223,39 @@
     assert all(need <= set(i) for i in d["items"])
     rank = {"CRITICAL": 0, "WARNING": 1, "UNKNOWN": 2, "OK": 3}
     assert [rank[i["status"]] for i in d["items"]] == sorted(rank[i["status"]] for i in d["items"])
-    crac = next(i for i in d["items"] if i["name"] == "CRAC Unit 1")
-    assert crac["status"] != "CRITICAL" and crac["active_criticals"] == 0
-    assert crac["criticals"] >= 1 and crac["faults"] >= 1  # recovered event remains visible as history
+
+    # Use a dedicated fixture so this check has the same meaning whether it runs
+    # before or after another test has created active alerts on the seeded CRAC.
+    base_asset = one("""SELECT a.location_id, a.system_type_id FROM assets a
+                       JOIN system_types st USING(system_type_id) WHERE st.code='HVAC' LIMIT 1""")
+    fixture_name = f"Recovered Test Asset {uuid.uuid4().hex[:8]}"
+    fixture_code = f"TEST-RECOVERED-{uuid.uuid4().hex[:8]}"
+    fixture_id = one("""INSERT INTO assets(asset_code,name,asset_type,system_type_id,location_id)
+                       VALUES (%s,%s,'sensor',%s,%s) RETURNING asset_id""",
+                     fixture_code, fixture_name, base_asset["system_type_id"], base_asset["location_id"])["asset_id"]
+    fault_id = one("SELECT event_type_id FROM event_types WHERE name='hvac_fault'")["event_type_id"]
+    recovery_id = one("SELECT event_type_id FROM event_types WHERE name='hvac_recovered'")["event_type_id"]
+    anchor = one("SELECT max(ts) AS t FROM (SELECT ts FROM telemetry_records UNION ALL SELECT ts FROM event_records) u")["t"]
+    fault_ts, recovery_ts = anchor + timedelta(seconds=1), anchor + timedelta(minutes=1)
+    DB.execute("""INSERT INTO event_records(asset_id,event_type_id,ts,severity,details)
+                   VALUES (%s,%s,%s,'critical','{}'::jsonb),(%s,%s,%s,'info','{}'::jsonb)""",
+               (fixture_id, fault_id, fault_ts, fixture_id, recovery_id, recovery_ts))
+    try:
+        refreshed = client.get("/dashboard/equipment-health", params={"hours": 72}).json()
+        recovered = next(i for i in refreshed["items"] if i["name"] == fixture_name)
+        assert recovered["status"] != "CRITICAL" and recovered["active_criticals"] == 0
+        assert recovered["criticals"] == 1 and recovered["faults"] == 1
+
+        crac = next(i for i in refreshed["items"] if i["name"] == "CRAC Unit 1")
+        open_critical_alerts = one("""SELECT count(*) AS n FROM alerts WHERE asset_id=%s
+                                    AND severity='critical' AND status IN ('open','acknowledged')""",
+                                   next(i["asset_id"] for i in refreshed["items"] if i["name"] == "CRAC Unit 1"))["n"]
+        assert (crac["status"] == "CRITICAL") == (open_critical_alerts > 0)
+        assert crac["active_criticals"] >= open_critical_alerts
+    finally:
+        DB.execute("DELETE FROM event_records WHERE asset_id=%s", (fixture_id,))
+        DB.execute("DELETE FROM assets WHERE asset_id=%s", (fixture_id,))
+
     healthy = next(i for i in d["items"] if i["name"] == "Lobby Camera")
     assert healthy["status"] == "OK" and healthy["last_seen"]
     only = client.get("/dashboard/equipment-health", params={"hours": 72, "system_type": "CCTV"}).json()
```

### §13.3 vector-to-test mapping

The 16 vectors are implemented as one database-backed aggregate test, not 16 separately named test functions. Each vector below maps to the same test name and its numbered inline assertion block in `tests/integration/test_frontend_api.py`:

`contract §13.3: all 16 conformance vectors plus 0.4 validation, audit, and quality semantics`

1. Valid 3-record batch — vector block 1.
2. Exact batch replay — block 2.
3. Changed payload with same batch ID — block 2.
4. Same telemetry key and value under a new batch — block 2.
5. Conflicting telemetry value preserves first write — block 2.
6. Non-UTC-offset timestamp syntax — block 6–7.
7. Future timestamp — block 6–7.
8. Incompatible `door_forced` event on camera — block 8–12.
9. Fahrenheit unit mismatch — block 8–12.
10. Unmapped external ID and rejection audit/view — block 8–12.
11. Invalid `quality: MISSING` — block 8–12.
12. Invalid state containing only `communication_status` — block 8–12.
13. 1,000 records with 10 rejected — block 13.
14. 5,001 records — block 14–16.
15. Source mismatch — block 14–16.
16. Empty batch updates source liveness — block 14–16.

The test asserts each vector inline; stdout reports the aggregate test name and a single PASS line when all assertions succeed. The regression run below records that aggregate result. No separate test names were added.

### Docs found stale or internally inconsistent; not edited in this step

- `docs/BASELINE_TEST_REPORT.md:31` says general ingestion and external mapping are not implemented. Its banner marks it historical/superseded; that line describes its old reviewed commit, not the current code.
- `docs/MODULAR_ARCHITECTURE.md:63` says the reviewed implementation has no general ingestion or external mapping interface. The document is a proposed architecture and is stale as a present-tense statement.
- `docs/REPOSITORY_RESTRUCTURE_PLAN.md:21,95` describes the current API as lacking general ingestion and external identity mapping. Its observations are pinned to the old `final` branch and need an explicit historical label or reconciliation.
- `docs/DATABASE_BASELINE.md:79,83` correctly describes the pre-006 schema, but calls `asset_external_ids` / `data_sources` “PROPOSED” without stating that this is only the historical baseline.
- `docs/HISTORIAN_DESIGN.md:64,71` marks provenance columns and source/batch tables as proposed, while its status and §1 say migrations 006–008 and ingestion are implemented.
- `docs/DATA_MIGRATION_PLAN.md:5` names only migrations 006 and 007 in its status even though §4/§5 describe 008 as implemented. Its shared-database-not-migrated statement remains accurate.
- `docs/API_CONTRACT.md` and `docs/CANONICAL_DATA_MODEL.md` label their older API examples as historical/proposed and direct readers to `INGESTION_CONTRACT.md`; those labels are accurate and are not runtime contradictions.

## Disposable restore and isolation

The private Compose project used a dedicated `postgres:16` service/container `anzma-h0-rehearsal-db`, named volume `anzma-h0-rehearsal_h0_pgdata`, host binding `127.0.0.1:15433`, and a separate API container on `127.0.0.1:18002`. Backup files were mounted read-only at `/h0`; the repository was mounted read-only in the API container. No service from `anzma-insight`, `anzma-d3-verify`, or `anzma-acceptance-441110d` was used. The `docker ps` inspection showed those existing stacks separately and the rehearsal ports bound only to loopback.

Restore command:

```sh
docker exec anzma-h0-rehearsal-db pg_restore --verbose --no-owner --no-privileges \
  -U postgres -d anzma_h0_rehearsal /h0/anzma_H0_20261118_1956.dump
```

Observed result: exit status `0`; the captured verbose stream had 102 lines. Beginning and ending output:

```text
pg_restore: connecting to database for restore
pg_restore: creating TABLE "public.alerts"
pg_restore: creating SEQUENCE "public.alerts_alert_id_seq"
pg_restore: creating TABLE "public.assets"
pg_restore: creating SEQUENCE "public.assets_asset_id_seq"
pg_restore: creating TABLE "public.buildings"
pg_restore: creating SEQUENCE "public.buildings_building_id_seq"
pg_restore: creating TABLE "public.event_records"
...
pg_restore: creating FK CONSTRAINT "public.event_records event_records_asset_id_fkey"
pg_restore: creating FK CONSTRAINT "public.event_records event_records_event_type_id_fkey"
pg_restore: creating FK CONSTRAINT "public.event_type_system_compat event_type_system_compat_event_type_id_fkey"
pg_restore: creating FK CONSTRAINT "public.event_type_system_compat event_type_system_compat_system_type_id_fkey"
pg_restore: creating FK CONSTRAINT "public.incidents incidents_location_id_fkey"
pg_restore: creating FK CONSTRAINT "public.locations locations_building_id_fkey"
pg_restore: creating FK CONSTRAINT "public.metric_type_system_compat metric_type_system_compat_metric_id_fkey"
pg_restore: creating FK CONSTRAINT "public.metric_type_system_compat metric_type_system_compat_system_type_id_fkey"
pg_restore: creating FK CONSTRAINT "public.telemetry_records telemetry_records_asset_id_fkey"
pg_restore: creating FK CONSTRAINT "public.telemetry_records telemetry_records_metric_id_fkey"
```

The complete captured output follows. The restored pre-migration public schema had 13 base tables and no `schema_migrations` relation. `event_records` already had its legacy unique constraint; `incidents`, `v_camera_status`, and `uq_alerts_rule_asset_first_ts` were also present, confirming the restored 003–005 baseline.

## Migration runner evidence

Commands:

```sh
docker exec anzma-h0-rehearsal-api python scripts/apply_migrations.py
docker exec anzma-h0-rehearsal-api python scripts/apply_migrations.py
docker exec anzma-h0-rehearsal-db psql -X -U postgres -d anzma_h0_rehearsal -c 'TABLE schema_migrations;'
```

The runner source enumerates repository migration files but skips any whose numeric prefix is below 6. It does not execute `db/init/anzma_schema_and_seed.sql` or migrations 003–005. The restored database already contained their schema objects and the 003–005 data counts before invocation.

First-run output:

```text
No schema_migrations ledger yet; applying additive migration 006 to create it.
APPLIED 006_ingestion_foundation
APPLIED 007_event_source_identity
APPLIED 008_ingestion_contract_conformance
```

Second-run output:

```text
SKIP 006_ingestion_foundation (already recorded)
SKIP 007_event_source_identity (already recorded)
SKIP 008_ingestion_contract_conformance (already recorded)
```

Ledger after the first run:

```text
              version               |          applied_at
------------------------------------+-------------------------------
 006_ingestion_foundation           | 2026-11-19 09:45:08.759358+00
 007_event_source_identity          | 2026-11-19 09:45:08.794134+00
 008_ingestion_contract_conformance | 2026-11-19 09:45:08.797212+00
(3 rows)
```

### Row counts

The supplied H0 file lists nine operational tables; they matched the restored database before migrations. All 13 pre-existing base-table counts were also captured before and after migrations, before the ingestion exercise:

| Table | H0 file | Restored before | After 006–008 | Result |
|---|---:|---:|---:|---|
| alerts | 19 | 19 | 19 | unchanged |
| assets | 12 | 12 | 12 | unchanged |
| buildings | 2 | 2 | 2 | unchanged |
| event_records | 955 | 955 | 955 | unchanged |
| event_type_system_compat | — | 16 | 16 | unchanged |
| event_types | 12 | 12 | 12 | unchanged |
| incidents | 15 | 15 | 15 | unchanged |
| locations | 6 | 6 | 6 | unchanged |
| metric_type_system_compat | — | 8 | 8 | unchanged |
| metric_types | 8 | 8 | 8 | unchanged |
| scenario_runs | — | 0 | 0 | unchanged |
| system_types | — | 5 | 5 | unchanged |
| telemetry_records | 4669 | 4669 | 4669 | unchanged |

Migration-created tables had zero rows after migration except `schema_migrations`, which had the three ledger rows shown above. The later API exercise intentionally added test rows to the disposable clone; those are after this count-preservation check.

## Schema and legacy-data checks

`\d event_records` before migration:

```text
                                  Table "public.event_records"
    Column     |           Type           | Collation | Nullable |           Default
---------------+--------------------------+-----------+----------+------------------------------
 event_id      | bigint                   |           | not null | generated always as identity
 asset_id      | bigint                   |           | not null |
 event_type_id | bigint                   |           | not null |
 ts            | timestamp with time zone |           | not null |
 severity      | text                     |           | not null |
 details       | jsonb                    |           |          |
Indexes:
    "event_records_pkey" PRIMARY KEY, btree (event_id)
    "event_records_asset_id_event_type_id_ts_key" UNIQUE CONSTRAINT, btree (asset_id, event_type_id, ts)
    "idx_event_asset_ts" btree (asset_id, ts)
    "idx_event_ts" btree (ts)
Check constraints:
    "event_records_severity_check" CHECK (severity = ANY (ARRAY['info'::text, 'warning'::text, 'critical'::text]))
Foreign-key constraints:
    "event_records_asset_id_fkey" FOREIGN KEY (asset_id) REFERENCES assets(asset_id)
    "event_records_event_type_id_fkey" FOREIGN KEY (event_type_id) REFERENCES event_types(event_type_id)
```

`\d event_records` after migration:

```text
                                    Table "public.event_records"
       Column       |           Type           | Collation | Nullable |           Default
--------------------+--------------------------+-----------+----------+------------------------------
 event_id           | bigint                   |           | not null | generated always as identity
 asset_id           | bigint                   |           | not null |
 event_type_id      | bigint                   |           | not null |
 ts                 | timestamp with time zone |           | not null |
 severity           | text                     |           | not null |
 details            | jsonb                    |           |          |
 source_id          | bigint                   |           |          |
 received_at        | timestamp with time zone |           |          |
 source_event_id    | text                     |           |          |
 ingestion_batch_id | bigint                   |           |          |
 contract_version   | text                     |           |          |
 source_metadata    | jsonb                    |           |          |
Indexes:
    "event_records_pkey" PRIMARY KEY, btree (event_id)
    "idx_event_asset_ts" btree (asset_id, ts)
    "idx_event_source_received" btree (source_id, received_at)
    "idx_event_ts" btree (ts)
    "uq_event_legacy_natural_key" UNIQUE, btree (asset_id, event_type_id, ts) WHERE source_event_id IS NULL
    "uq_event_source_event_id" UNIQUE, btree (source_id, source_event_id) WHERE source_id IS NOT NULL AND source_event_id IS NOT NULL
Check constraints:
    "event_records_severity_check" CHECK (severity = ANY (ARRAY['info'::text, 'warning'::text, 'critical'::text]))
Foreign-key constraints:
    "event_records_asset_id_fkey" FOREIGN KEY (asset_id) REFERENCES assets(asset_id)
    "event_records_event_type_id_fkey" FOREIGN KEY (event_type_id) REFERENCES event_types(event_type_id)
    "event_records_ingestion_batch_id_fkey" FOREIGN KEY (ingestion_batch_id) REFERENCES ingestion_batches(ingestion_batch_id) ON DELETE RESTRICT
    "event_records_source_id_fkey" FOREIGN KEY (source_id) REFERENCES data_sources(source_id) ON DELETE RESTRICT
```

The full `psql \d` output differed only by the expanded CHECK expression; `...` above abbreviates the unchanged severity check's displayed expression. Migration 007 removed the full natural-key constraint and created the partial legacy key plus source-event identity index.

Legacy row semantics after migration:

```text
 telemetry_rows | null_quality | valid_view_rows
----------------+--------------+-----------------
           4669 |         4669 |            4669
(1 row)

 legacy_event_rows
-------------------
               955
(1 row)
```

Thus every pre-existing telemetry row retained `quality IS NULL`, all 4669 remained visible through `v_telemetry_valid`, and all 955 pre-existing events remained source-less. No historical fact row was updated or backfilled by the migrations.

Read-only schema verification command:

```sh
docker exec -i anzma-h0-rehearsal-db psql -X -U postgres -d anzma_h0_rehearsal < scripts/verify_schema.sql
```

Observed result: checks 1–10 were `OK`; check 99 was `OVERALL | all OK | OK` (11 rows total).

## API snapshot comparison

**Correction recorded 19 November 2026:** the original item 0.5 report said zero differences but did not preserve the response files, command, or hashes. That statement was too broad. A fresh H0 restore+migration reproduction on the isolated `anzma-item07-compare` stack captured raw endpoint bodies before adding ingestion test data. The comparison included every field; no paths were ignored.

Raw SHA-256 results:

```text
4d1afe88f69a06e9f5e160ad77de22fd31d1a0c41a483958e4c24cc63f31c152  H0_api_20261118_1959/dashboard_overview.json
2035226a11a30266c3e2dde2e29cb8b6179a3d8cb20e5fe645e0248601743726  item07-dashboard_overview.json
2b882b4e3b4ecfcc176b62c749ce219ee032cc5a5d3bc83e82e2d19ffad895b6  H0_api_20261118_1959/alerts.json
2b882b4e3b4ecfcc176b62c749ce219ee032cc5a5d3bc83e82e2d19ffad895b6  item07-alerts.json
43df1c64294565ad2fdc7141c6a6d40451ab098852ab4df9768421e34ba6c6d7  H0_api_20261118_1959/incidents.json
43df1c64294565ad2fdc7141c6a6d40451ab098852ab4df9768421e34ba6c6d7  item07-incidents.json
```

Raw response capture commands (run inside the isolated API container, redirecting bytes to `/private/tmp`):

```sh
docker exec anzma-item07-compare-api-1 python -c "import urllib.request,sys; sys.stdout.buffer.write(urllib.request.urlopen('http://127.0.0.1:8000/dashboard/overview').read())" > /private/tmp/item07-dashboard_overview.json
docker exec anzma-item07-compare-api-1 python -c "import urllib.request,sys; sys.stdout.buffer.write(urllib.request.urlopen('http://127.0.0.1:8000/alerts?limit=50&offset=0').read())" > /private/tmp/item07-alerts.json
docker exec anzma-item07-compare-api-1 python -c "import urllib.request,sys; sys.stdout.buffer.write(urllib.request.urlopen('http://127.0.0.1:8000/incidents').read())" > /private/tmp/item07-incidents.json
sha256sum /Users/test/anzma_backups/H0_api_20261118_1959/*.json /private/tmp/item07-*.json
```

Raw unified diff command (the minified JSON bodies are each one long line):

```text
diff -u /Users/test/anzma_backups/H0_api_20261118_1959/dashboard_overview.json /private/tmp/item07-dashboard_overview.json || true
```

The one changed dashboard value in that raw line was `"avg_zone_temp","value":17.872061855670097` versus `"avg_zone_temp","value":17.8720618556701`. The recursive JSON comparison command used to locate the path was:

```sh
python3 - <<'PY'
import json
from pathlib import Path
pairs=[('/Users/test/anzma_backups/H0_api_20261118_1959/dashboard_overview.json','/private/tmp/item07-dashboard_overview.json'),('/Users/test/anzma_backups/H0_api_20261118_1959/alerts.json','/private/tmp/item07-alerts.json'),('/Users/test/anzma_backups/H0_api_20261118_1959/incidents.json','/private/tmp/item07-incidents.json')]
def walk(a,b,path='$'):
 out=[]
 if type(a) is not type(b): return [(path,a,b)]
 if isinstance(a,dict):
  for k in sorted(set(a)|set(b)):
   if k not in a or k not in b: out.append((path+'.'+k,a.get(k,'<MISSING>'),b.get(k,'<MISSING>')))
   else: out.extend(walk(a[k],b[k],path+'.'+k))
 elif isinstance(a,list):
  if len(a)!=len(b): out.append((path+'.length',len(a),len(b)))
  for i,(x,y) in enumerate(zip(a,b)): out.extend(walk(x,y,f'{path}[{i}]'))
 elif a!=b: out.append((path,a,b))
 return out
for old,new in pairs:
 diffs=walk(json.loads(Path(old).read_text()),json.loads(Path(new).read_text()))
 print(f'{Path(old).name}: differences={len(diffs)}')
 for d in diffs: print(d)
PY
```

Observed recursive output:

```text
dashboard_overview.json: differences=1
('$.kpis[5].value', 17.872061855670097, 17.8720618556701)
alerts.json: differences=0
incidents.json: differences=0
```

The dashboard value is the average zone temperature and differs by about `3e-15` (only the final floating-point digit); every other dashboard field matches. The alerts and incidents raw files are byte-identical to their snapshots. The original statement “zero differences” is superseded by this correction. No time-dependent fields were ignored.

| Snapshot | Endpoint | Differences |
|---|---|---:|
| `dashboard_overview.json` | `GET /dashboard/overview` | 1 numeric serialization/precision delta |
| `alerts.json` | `GET /alerts?limit=50&offset=0` | 0 |
| `incidents.json` | `GET /incidents` | 0 |

## Ingestion and backward-compatibility path

The source/key and mapping were created using `scripts/ingestion_admin.py` inside the API container. A one-time key was held in the harness process and redacted from output; PostgreSQL stores only the key hash. An initial harness attempt used an incorrect output-prefix parser after creating an unused source. That unused key was explicitly revoked with `ingestion_admin.py revoke-key`; the source, key, mapping, batches, and test facts exist only in the disposable database.

Observed request results (batch UUIDs and IDs are from the run; one-time credentials omitted):

```text
valid HTTP 200
status=ACCEPTED transaction=COMMITTED counts={received:1, accepted:1, duplicate:0, rejected:0}
result={index:0, outcome:ACCEPTED, record_id:4670, record_type:telemetry}

exact replay HTTP 200
Idempotent-Replay: true
parsed response JSON equals the original saved response: true

partial HTTP 200
status=PARTIAL transaction=COMMITTED counts={received:2, accepted:1, duplicate:0, rejected:1}
record 0=ACCEPTED; record 1=REJECTED reason_code=UNIT_MISMATCH

conflicting duplicate HTTP 422
status=REJECTED transaction=COMMITTED counts={received:1, accepted:0, duplicate:0, rejected:1}
reason_code=CONFLICT; "first write was preserved"
stored accepted measurement after conflict: {value:20.0, quality:GOOD}

legacy POST /events HTTP 201
{"asset_id":1,"event_type":"hvac_fault","id":956,"severity":"INFO","ts":"2001-01-01T00:00:00Z"}

legacy POST /alerts HTTP 201
{"asset_id":1,"id":5061,"rule_id":"HVAC_HIGH_TEMP","status":"OPEN", ...}
```

The partial and conflict response bodies stated `transaction: COMMITTED`, accurately reflecting that rejected-record audit and accepted records were committed. The route remained `/api/v1/ingest/batches`; legacy endpoints continued returning their existing success status codes.

## Regression runs

All database-backed suites below ran with `PGHOST=db` and `PGDATABASE` values inside the private rehearsal network/container. Their test databases were separate from the restored H0 database unless explicitly noted. Results:

| Suite | Result |
|---|---:|
| `tests/unit/test_day2_ahmed.py` | 28/28 passed |
| `tests/integration/test_data_integrity.py` | 23/23 passed |
| `tests/integration/test_day2_integration.py` | 43/43 passed |
| `tests/integration/test_chat_consistency.py` | 31/31 passed |
| `tests/integration/test_frontend_api.py` | 21/21 passed, including the aggregate test covering all 16 vectors and the legacy POST routes |
| `tests/scenarios/e2e_s1.py --build` | 14/14 checks passed |

The separate `tests/integration/test_dashboard.py` smoke script had **no passing result** against the H0 dataset (attempted; NOT PASS): it calls the dashboard with its default full data span, which exceeds the API's 31-day maximum and raised `DashboardError: The period may not be longer than 31 days.` No code or data was changed to bypass that guard. This limit and the script's missing range parameter are recorded here rather than reported as a pass.

## Scope stop

Item 0.5 rehearsal and evidence report are complete. The shared/live database was not migrated. Migration application there remains a separate operator-run action. No work on 0.7 or Phase 1 was started.

### Complete `pg_restore --verbose` output

```text
pg_restore: connecting to database for restore
pg_restore: creating TABLE "public.alerts"
pg_restore: creating SEQUENCE "public.alerts_alert_id_seq"
pg_restore: creating TABLE "public.assets"
pg_restore: creating SEQUENCE "public.assets_asset_id_seq"
pg_restore: creating TABLE "public.buildings"
pg_restore: creating SEQUENCE "public.buildings_building_id_seq"
pg_restore: creating TABLE "public.event_records"
pg_restore: creating SEQUENCE "public.event_records_event_id_seq"
pg_restore: creating TABLE "public.event_type_system_compat"
pg_restore: creating TABLE "public.event_types"
pg_restore: creating SEQUENCE "public.event_types_event_type_id_seq"
pg_restore: creating TABLE "public.incidents"
pg_restore: creating SEQUENCE "public.incidents_incident_id_seq"
pg_restore: creating TABLE "public.locations"
pg_restore: creating SEQUENCE "public.locations_location_id_seq"
pg_restore: creating TABLE "public.metric_type_system_compat"
pg_restore: creating TABLE "public.metric_types"
pg_restore: creating SEQUENCE "public.metric_types_metric_id_seq"
pg_restore: creating TABLE "public.scenario_runs"
pg_restore: creating SEQUENCE "public.scenario_runs_scenario_run_id_seq"
pg_restore: creating TABLE "public.system_types"
pg_restore: creating SEQUENCE "public.system_types_system_type_id_seq"
pg_restore: creating TABLE "public.telemetry_records"
pg_restore: creating SEQUENCE "public.telemetry_records_telemetry_id_seq"
pg_restore: creating VIEW "public.v_alert_overview"
pg_restore: creating VIEW "public.v_data_clock"
pg_restore: creating VIEW "public.v_asset_overview"
pg_restore: creating VIEW "public.v_camera_status"
pg_restore: creating VIEW "public.v_incident_overview"
pg_restore: processing data for table "public.alerts"
pg_restore: processing data for table "public.assets"
pg_restore: processing data for table "public.buildings"
pg_restore: processing data for table "public.event_records"
pg_restore: processing data for table "public.event_type_system_compat"
pg_restore: processing data for table "public.event_types"
pg_restore: processing data for table "public.incidents"
pg_restore: processing data for table "public.locations"
pg_restore: processing data for table "public.metric_type_system_compat"
pg_restore: processing data for table "public.metric_types"
pg_restore: processing data for table "public.scenario_runs"
pg_restore: processing data for table "public.system_types"
pg_restore: processing data for table "public.telemetry_records"
pg_restore: executing SEQUENCE SET alerts_alert_id_seq
pg_restore: executing SEQUENCE SET assets_asset_id_seq
pg_restore: executing SEQUENCE SET buildings_building_id_seq
pg_restore: executing SEQUENCE SET event_records_event_id_seq
pg_restore: executing SEQUENCE SET event_types_event_type_id_seq
pg_restore: executing SEQUENCE SET incidents_incident_id_seq
pg_restore: executing SEQUENCE SET locations_location_id_seq
pg_restore: executing SEQUENCE SET metric_types_metric_id_seq
pg_restore: executing SEQUENCE SET scenario_runs_scenario_run_id_seq
pg_restore: executing SEQUENCE SET system_types_system_type_id_seq
pg_restore: executing SEQUENCE SET telemetry_records_telemetry_id_seq
pg_restore: creating CONSTRAINT "public.alerts alerts_pkey"
pg_restore: creating CONSTRAINT "public.assets assets_asset_code_key"
pg_restore: creating CONSTRAINT "public.assets assets_pkey"
pg_restore: creating CONSTRAINT "public.buildings buildings_name_key"
pg_restore: creating CONSTRAINT "public.buildings buildings_pkey"
pg_restore: creating CONSTRAINT "public.event_records event_records_asset_id_event_type_id_ts_key"
pg_restore: creating CONSTRAINT "public.event_records event_records_pkey"
pg_restore: creating CONSTRAINT "public.event_type_system_compat event_type_system_compat_pkey"
pg_restore: creating CONSTRAINT "public.event_types event_types_name_key"
pg_restore: creating CONSTRAINT "public.event_types event_types_pkey"
pg_restore: creating CONSTRAINT "public.incidents incidents_pkey"
pg_restore: creating CONSTRAINT "public.locations locations_building_id_name_key"
pg_restore: creating CONSTRAINT "public.locations locations_pkey"
pg_restore: creating CONSTRAINT "public.metric_type_system_compat metric_type_system_compat_pkey"
pg_restore: creating CONSTRAINT "public.metric_types metric_types_name_key"
pg_restore: creating CONSTRAINT "public.metric_types metric_types_pkey"
pg_restore: creating CONSTRAINT "public.scenario_runs scenario_runs_pkey"
pg_restore: creating CONSTRAINT "public.system_types system_types_code_key"
pg_restore: creating CONSTRAINT "public.system_types system_types_pkey"
pg_restore: creating CONSTRAINT "public.telemetry_records telemetry_records_asset_id_metric_id_ts_key"
pg_restore: creating CONSTRAINT "public.telemetry_records telemetry_records_pkey"
pg_restore: creating INDEX "public.idx_alerts_asset_ts"
pg_restore: creating INDEX "public.idx_alerts_first_ts"
pg_restore: creating INDEX "public.idx_alerts_incident"
pg_restore: creating INDEX "public.idx_alerts_open"
pg_restore: creating INDEX "public.idx_alerts_severity"
pg_restore: creating INDEX "public.idx_assets_location"
pg_restore: creating INDEX "public.idx_assets_system_type"
pg_restore: creating INDEX "public.idx_event_asset_ts"
pg_restore: creating INDEX "public.idx_event_ts"
pg_restore: creating INDEX "public.idx_incidents_location_last"
pg_restore: creating INDEX "public.idx_incidents_status"
pg_restore: creating INDEX "public.idx_telemetry_ts"
pg_restore: creating INDEX "public.uq_alerts_rule_asset_first_ts"
pg_restore: creating FK CONSTRAINT "public.alerts alerts_asset_id_fkey"
pg_restore: creating FK CONSTRAINT "public.alerts alerts_incident_id_fkey"
pg_restore: creating FK CONSTRAINT "public.assets assets_location_id_fkey"
pg_restore: creating FK CONSTRAINT "public.assets assets_system_type_id_fkey"
pg_restore: creating FK CONSTRAINT "public.event_records event_records_asset_id_fkey"
pg_restore: creating FK CONSTRAINT "public.event_records event_records_event_type_id_fkey"
pg_restore: creating FK CONSTRAINT "public.event_type_system_compat event_type_system_compat_event_type_id_fkey"
pg_restore: creating FK CONSTRAINT "public.event_type_system_compat event_type_system_compat_system_type_id_fkey"
pg_restore: creating FK CONSTRAINT "public.incidents incidents_location_id_fkey"
pg_restore: creating FK CONSTRAINT "public.locations locations_building_id_fkey"
pg_restore: creating FK CONSTRAINT "public.metric_type_system_compat metric_type_system_compat_metric_id_fkey"
pg_restore: creating FK CONSTRAINT "public.metric_type_system_compat metric_type_system_compat_system_type_id_fkey"
pg_restore: creating FK CONSTRAINT "public.telemetry_records telemetry_records_asset_id_fkey"
pg_restore: creating FK CONSTRAINT "public.telemetry_records telemetry_records_metric_id_fkey"
```
