# PostgreSQL restore runbook

This runbook covers a verified scratch restore and the operator procedure for a database recovery incident. The C2 verification below ran on a disposable database only; no shared or live database was restored or switched.

## Preconditions

- Identify the intended Compose project, Compose file, database service, and protected backup directory. The commands below assume the repository root and the existing `.env` used by Compose.
- Confirm the dump and its `.sha256` and `.rowcounts.txt` sidecars are present. Keep them protected; the logical archive contains database contents.
- Keep connectors stopped or paused during an incident restore. A successful scratch verification does not stop writes to a live database.
- Use a scratch name beginning with `anzma_restore_check` that differs from `PGDATABASE` and does not already exist. The verifier enforces these guards.

## Verify a backup without changing the source database

Run from the repository root. Set the Compose project and `ANZMA_BACKUP_DIR` for the intended deployment; do not expose secrets in the terminal transcript.

```sh
ANZMA_COMPOSE_PROJECT=anzma-phase0-c2-restore \
ANZMA_BACKUP_DIR="$HOME/anzma_backups/anzma-phase0-staging" \
ANZMA_RESTORE_SCRATCH_DB=anzma_restore_check_manual \
scripts/restore_verify.sh --backup "$HOME/anzma_backups/anzma-phase0-staging/anzma_20261119_145637.dump"
```

The script checks the checksum and archive listing before creating the scratch database; restores into that new database; checks `scripts/verify_schema.sql`, the migration ledger, and every table in the row-count sidecar; then drops only its scratch database. Use `--dry-run` to inspect the plan without creating a database. A final JSON line with `dry_run: 1` and `RESTORE_VERIFY_OK` means only that prechecks and the plan passed; no restore was attempted. Use `--keep` only when retaining the scratch database is intentional. Read the final JSON line: `RESTORE_VERIFY_OK` means all checks passed; `RESTORE_VERIFY_FAILED` means a check failed. Inspect stderr for the named schema/table mismatch. Exit code `2` means configuration or a safety guard refused to proceed; exit code `1` means verification failed.

Host mode uses locally installed PostgreSQL client tools and standard `PG*` environment variables:

```sh
ANZMA_BACKUP_MODE=host PGHOST=127.0.0.1 PGPORT=5432 PGUSER=anzma PGDATABASE=anzma \
scripts/restore_verify.sh --backup /secure/backups/anzma_YYYYmmdd_HHMMSS.dump
```

Host mode was **NOT TESTED** in this repository; it requires the host client tools and a connection to the intended disposable database. Never point it at the shared/live database for verification.

## Incident restore and cutover

The following is an operator procedure, **NOT RUN in this repository**. It switches the configured deployment database and causes service interruption. Confirm the incident decision, preserve the current database, and coordinate the maintenance window before cutover.

1. Stop the API service and stop/pause all connectors and other writers. Take and preserve a final backup of the current database if it remains readable. Keep the API stopped throughout cutover.
2. Run `scripts/restore_verify.sh --backup /secure/path/backup.dump --keep` against the intended deployment project. Record the scratch database name from the output and require `RESTORE_VERIFY_OK`. If verification fails, do not switch.
3. Choose a unique archival name, for example `anzma_pre_restore_20261119`. From the repository root, terminate connections only to the current database and rename it aside. These are separate `psql -c` calls because PostgreSQL database rename cannot run inside a transaction:

   ```sh
   docker compose --env-file .env -p anzma-insight -f deploy/compose/docker-compose.yml stop api
   docker compose --env-file .env -p anzma-insight -f deploy/compose/docker-compose.yml exec -T db \
     sh -c 'psql -U "$POSTGRES_USER" -d postgres' <<'SQL'
   SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='anzma' AND pid <> pg_backend_pid();
   SQL
   docker compose --env-file .env -p anzma-insight -f deploy/compose/docker-compose.yml exec -T db \
     sh -c 'psql -U "$POSTGRES_USER" -d postgres' <<'SQL'
   ALTER DATABASE anzma RENAME TO anzma_pre_restore_20261119;
   SQL
   ```

4. **Cutover (reversible while writers remain stopped):** rename the verified scratch database to the configured production database name. This changes which database the application will use after it starts:

   ```sh
   docker compose --env-file .env -p anzma-insight -f deploy/compose/docker-compose.yml exec -T db \
     sh -c 'psql -U "$POSTGRES_USER" -d postgres' <<'SQL'
   ALTER DATABASE anzma_restore_check_manual RENAME TO anzma;
   SQL
   ```

   If this second rename fails, leave the API stopped and rename `anzma_pre_restore_20261119` back to `anzma`. Keep both databases until the incident is resolved. Replace the example names with the actual names chosen in steps 2–3.
5. Restart the API, confirm `/health` and `/ready`, then perform the approved ingestion probe below. Resume connectors only after the checks pass.

### Roll back the cutover

Before resuming writes, stop the API and connectors, then run these commands with a unique failed-restore name:

```sh
docker compose --env-file .env -p anzma-insight -f deploy/compose/docker-compose.yml stop api
docker compose --env-file .env -p anzma-insight -f deploy/compose/docker-compose.yml exec -T db \
  sh -c 'psql -U "$POSTGRES_USER" -d postgres' <<'SQL'
SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='anzma' AND pid <> pg_backend_pid();
SQL
docker compose --env-file .env -p anzma-insight -f deploy/compose/docker-compose.yml exec -T db \
  sh -c 'psql -U "$POSTGRES_USER" -d postgres' <<'SQL'
ALTER DATABASE anzma RENAME TO anzma_failed_restore_20261119;
SQL
docker compose --env-file .env -p anzma-insight -f deploy/compose/docker-compose.yml exec -T db \
  sh -c 'psql -U "$POSTGRES_USER" -d postgres' <<'SQL'
ALTER DATABASE anzma_pre_restore_20261119 RENAME TO anzma;
SQL
```

The previous database is preserved by the rename. **The operational point of no return for a lossless rollback is resuming writes on the restored database:** switching back afterward loses those new writes unless they are separately captured and reconciled. Do not drop either database as part of an automatic rollback.

## Post-restore checks

- Request `/health` (process liveness) and `/ready` (database readiness) through the deployed API endpoint.
- Use a designated test source, its protected API key, and a mapped test asset for one valid, small ingestion batch. Confirm the expected accepted response and retrieve its batch outcome; do not print the key. This probe changes the restored database and should be coordinated with the operator.
- `scripts/compare_api_snapshots.py BEFORE.json AFTER.json` compares two JSON files and tolerates only its configured tiny numeric noise. It is useful only when comparable pre-incident and post-restore API snapshots exist for equivalent data/time filters. It does not query the API or prove database integrity; it was **NOT RUN** in the C2 scratch restore.
- A `pg_dump` is a logical database snapshot at one point in time. It does not protect host configuration, environment secrets, external connector queues, log files, or Ollama model files. The recovery point objective (RPO) is the interval between backups; database writes after the last backup are lost on restore. A connector retry with the same `batch_id` can repopulate records only when its original batch is still retained by that connector and the restored database does not already contain that batch.

## Last verified scratch restore

| Date | Backup file | Result | Row counts in verified archive / restored scratch |
|---|---|---|---|
| 2026-11-19 | `anzma_20261119_145637.dump` | `RESTORE_VERIFY_OK`; scratch DB dropped; database list before/after was `anzma, postgres` | `telemetry_records=4669`, `event_records=955`, `alerts=19`, `incidents=15`, `assets=12`, `buildings=2`, `locations=6`, `data_sources=0`, `source_credentials=0`, `asset_external_ids=0`, `ingestion_batches=0`, `ingestion_rejections=0`, `equipment_state_records=0` |

The successful run compared all 13 listed tables with zero mismatches. It ran in the isolated Compose project `anzma-phase0-c2-restore`; API cutover and post-restore HTTP/ingestion probes were **NOT TESTED**.
