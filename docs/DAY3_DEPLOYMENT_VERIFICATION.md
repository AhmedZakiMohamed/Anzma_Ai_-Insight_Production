# Day 3 clean-clone deployment verification

**Date:** 19 November 2026 (UTC)  
**Branch:** `feature/ingestion-foundation`  
**Clone commit:** `07bf77ed8a915deeec500bb57d48479d959dd954`  
**Project:** `anzma-item07-clean-rerun`  
**Isolation:** new named volumes, PostgreSQL 16, API/Ollama containers belonging only to this Compose project. No existing/shared database was used.

## What was tested

A fresh local Git clone was installed using the root README, `docs/README.md`, `docs/RUN_DEMO.md`, and the v1 onboarding steps in `docs/INGESTION_CONTRACT.md`. Compose initialized schema, seed data, and migrations 003–008 on an empty volume. The migrated API became ready, and one source-authenticated telemetry batch was accepted.

This is a fresh clone and fresh-volume installation on the existing Docker host, not a second physical machine. The distinction is recorded in `DAY3_FUNCTIONAL_TEST_REPORT.md`.

## Initial documented install attempt and docs/config gap

The first attempt used the then-documented defaults from a clean clone at `abf44d18569b88ddb95e8710454b534a478e1443` and a new Compose project. The command was:

```sh
docker compose --env-file .env -p anzma-item07-clean -f deploy/compose/docker-compose.yml up -d --build
```

The build completed, but the stack did not start because existing stacks already held host port 5432. A follow-up compose attempt printed:

```text
Error response from daemon: failed to set up container networking on endpoint anzma-item07-clean-db-1 (...):
Bind for 0.0.0.0:5432 failed: port is already allocated
```

`docker compose ps -a` showed API and DB as `Created`, while the Ollama container alone had started. The 11434 default was also in use by that project's Ollama service during the attempt. No shared database was contacted. The failed project's containers and volumes were removed with:

```sh
docker compose --env-file .env -p anzma-item07-clean -f deploy/compose/docker-compose.yml down -v
```

The missing documented step was how to select free host ports when another local stack was running. The Compose file now accepts `DB_HOST_PORT`, `API_HOST_PORT`, and `OLLAMA_HOST_PORT`; the safe environment example and both README guides explain the defaults and overrides. The Compose port defaults still bind to all host interfaces; see [`../KNOWN_ISSUES.md`](../KNOWN_ISSUES.md#ki-006).

Other documentation gaps corrected before the successful run:

- Root README incorrectly said to apply only migration 006 to an existing volume; it now says 006–008.
- `docs/RUN_DEMO.md` omitted 006–008 from the fresh-volume migration list and did not direct existing volumes to the runner.
- Root README and `docs/README.md` omitted `/ready` and the DB readiness check.
- `docs/DEPLOYMENT_STANDARD.md` still described `/ready` and the Compose deployment as unverified and had no executed install commands.
- `docs/INGESTION_CONTRACT.md` described key security but had no operator sequence for registering a building, creating a source/key, mapping an asset, and sending a first batch. A step-by-step sequence was added.

## Successful clean-clone command sequence

Clone the branch and confirm the revision:

```sh
git clone --branch feature/ingestion-foundation --single-branch \
  /Users/test/Desktop/Anzma_Ai_Insight_Production /private/tmp/anzma-item07-clean-rerun
git -C /private/tmp/anzma-item07-clean-rerun rev-parse HEAD
```

Observed clone revision:

```text
07bf77ed8a915deeec500bb57d48479d959dd954
```

Create the local environment file and set a disposable non-empty password plus free host ports. The actual password value is intentionally not reproduced:

```sh
cd /private/tmp/anzma-item07-clean-rerun
cp deploy/examples/.env.example .env
# PGPASSWORD set to a disposable local value; host ports set to 15437, 18006, and 11435.
```

Start the new project from the clone with empty volumes:

```sh
docker compose --env-file .env -p anzma-item07-clean-rerun \
  -f deploy/compose/docker-compose.yml up -d --build
```

Observed output included:

```text
Volume "anzma-item07-clean-rerun_pgdata" Creating
Volume "anzma-item07-clean-rerun_ollama_models" Creating
Container anzma-item07-clean-rerun-db-1 Started
Container anzma-item07-clean-rerun-db-1 Healthy
Container anzma-item07-clean-rerun-api-1 Started
Container anzma-item07-clean-rerun-ollama-1 Started
Container anzma-item07-clean-rerun-ollama-1 Healthy
Container anzma-item07-clean-rerun-ollama-init-1 Started
```

Check services:

```sh
docker compose --env-file .env -p anzma-item07-clean-rerun \
  -f deploy/compose/docker-compose.yml ps
```

Observed service state:

```text
NAME                                     SERVICE       STATUS                    PORTS
anzma-item07-clean-rerun-api-1           api           Up (healthy)              0.0.0.0:18006->8000/tcp
anzma-item07-clean-rerun-db-1            db            Up (healthy)              0.0.0.0:15437->5432/tcp
anzma-item07-clean-rerun-ollama-1        ollama        Up (healthy)              0.0.0.0:11435->11434/tcp
anzma-item07-clean-rerun-ollama-init-1   ollama-init   Up (model pull running)   11434/tcp
```

The DB and API are healthy while `ollama-init` may still be downloading the configured model. The model pull is not required for dashboard/API database readiness.

Check PostgreSQL and HTTP endpoints:

```sh
docker compose --env-file .env -p anzma-item07-clean-rerun \
  -f deploy/compose/docker-compose.yml exec -T db pg_isready -U postgres -d anzma
curl -i --max-time 10 http://127.0.0.1:18006/health
curl -i --max-time 10 http://127.0.0.1:18006/ready
```

Observed output:

```text
/var/run/postgresql:5432 - accepting connections
HTTP/1.1 200 OK
{"status":"ok"}
HTTP/1.1 200 OK
{"status":"ready","database":"up"}
```

The Docker API image healthcheck also reached `/ready`; Compose reported `api Up (healthy)`. The liveness endpoint alone does not prove DB availability, so both HTTP endpoints were queried.

Fresh-init migration ledger:

```text
006_ingestion_foundation           | 2026-11-19 10:11:21.591488+00
007_event_source_identity          | 2026-11-19 10:11:21.638646+00
008_ingestion_contract_conformance | 2026-11-19 10:11:21.657643+00
(3 rows)
```

The mounts in Compose initialize base/003–005 and apply 006–008 when PostgreSQL initializes an empty volume. Existing volumes need the explicit runner procedure in `DATA_MIGRATION_PLAN.md`; init mounts do not upgrade them.

## First ingestion batch

The clean database contained `Main Tower` as building 1 and `HVAC-AHU-01` (`Air Handler 1`) as an asset in that building. Commands executed in the API container:

```sh
python scripts/ingestion_admin.py register-building --building-id 1 --building-code CLEAN-INSTALL-1
python scripts/ingestion_admin.py create-source --source-code clean-install-demo \
  --building-code CLEAN-INSTALL-1 --connector-name demo --connector-version 1
python scripts/ingestion_admin.py map-asset --source-code clean-install-demo \
  --external-id first-device --asset-code HVAC-AHU-01
```

CLI output (the one-time key is redacted; it was held only in process memory):

```text
Registered building 1 as CLEAN-INSTALL-1.
Source created: clean-install-demo
API key (shown once): [REDACTED ONE-TIME KEY]
Store this value in a secret manager; only its SHA-256 hash is stored in PostgreSQL.
Mapped clean-install-demo:first-device to HVAC-AHU-01.
```

The request was sent over HTTP to `POST /api/v1/ingest/batches` with contract `1.0`, a generated UUID batch ID, the current UTC timestamp, `source_code=clean-install-demo`, `building_code=CLEAN-INSTALL-1`, and one telemetry record: external ID `first-device`, metric `temperature`, value `22.5`, unit `C`. The authorization key was never printed.

Observed response:

```text
POST /api/v1/ingest/batches HTTP 200
{"batch_id":"a027e10a-ae53-40df-b6b5-db959d27ab67","contract_version":"1.0","counts":{"accepted":1,"duplicate":0,"received":1,"rejected":0},"received_at":"2026-11-19T10:12:20.000557Z","rejected":[],"rejected_truncated":false,"results":[{"index":0,"outcome":"ACCEPTED","record_id":4670,"record_type":"telemetry"}],"status":"ACCEPTED","transaction":"COMMITTED","warnings":[]}
```

The clean installation proved one authenticated first-batch path. It did not prove a live device connector, production security, backup/restore, or successful LLM inference.
