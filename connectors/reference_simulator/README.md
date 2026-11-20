# Reference simulator connector

This standalone package sends deterministic demo telemetry and events to Anzma Core over the versioned HTTP ingestion contract. It has no Core database access and imports no Core application code. Provision a source, building code, and source-scoped asset mappings separately with `scripts/ingestion_admin.py`.

## Run

From the repository root:

```sh
PYTHONPATH=connectors/reference_simulator python -m simulator.cli run --scenario normal --batches 1
```

Required settings are `ANZMA_SIMULATOR_CORE_URL`, `ANZMA_SIMULATOR_SOURCE_CODE`, `ANZMA_SIMULATOR_BUILDING_CODE`, and exactly one of `ANZMA_SIMULATOR_API_KEY` or `ANZMA_SIMULATOR_API_KEY_FILE`. `ANZMA_SIMULATOR_SEED_BUILDING` selects `main-tower` or `data-center-annex` from the seed catalogue and must match the provisioned source's building. A non-secret TOML file may be selected with `ANZMA_SIMULATOR_CONFIG_FILE`; environment values override its settings. The key is never stored with batches or printed. State defaults to `~/.anzma-simulator/outbox.sqlite3` in a private directory.

The one-shot scenarios are `normal`, `fault`, `stale-silent`, `bad-record`, `late-out-of-order`, and `duplicate-batch`. `--keepalive` sends an empty records batch. `--get-batch UUID` fetches the stored Core outcome. The Dockerfile runs the same module as a non-root user; mount `/var/lib/simulator` as a private persistent directory and supply secrets at runtime.

## Durable buffer, drain, and keepalive

SQLite uses WAL and `synchronous=FULL`. Startup runs `integrity_check` and verifies saved payload hashes before opening state read/write. Corruption stops generation and sending; the damaged file is left untouched. Exit code **3** is distinct. Preserve it for diagnosis and recover only from a known-good copy after operator review.

`ANZMA_SIMULATOR_BUFFER_MAX_BYTES` defaults to `268435456` (256 MiB), minimum 1024, and caps pending serialized payload bytes. At capacity, generation pauses; queued rows are never deleted or overwritten. If an input cannot be queued, `dropped_records` increments by its record count and is persisted for later `connector_status`. Status contains only `vendor_link: UNKNOWN`, pending `buffered_records`, cumulative `dropped_records`, and `last_poll_at`.

`ANZMA_SIMULATOR_KEEPALIVE_SECONDS` defaults to 300 and has a 10-second minimum. `run --loop` or `drain --loop` sends an empty `records` keepalive after that much time without a successful batch. A pending keepalive is reused, never duplicated; each new keepalive has a fresh `batch_id`.

`drain` sends pending rows oldest first, one in flight. Network errors, timeouts, 429, and all 5xx retry the exact stored bytes with 1-second exponential backoff, full jitter, and a 300-second cap; 429 waits at least integer `Retry-After` and uses the larger delay. `--max-iterations N` bounds attempts; exhaustion returns **5** and preserves pending rows. 401/403 pauses with exit **4**; correct/rotate the source key, then run `drain` again. `scripts/ingestion_admin.py rotate-key --source-code SOURCE` issues a replacement while the old key remains active; update the environment or secret file, then revoke the old prefix separately if desired. Keys are never written to the outbox, logs, or errors.

Terminal 400, 409, 413, and 422 batches are retained as failed with the redacted Core response; draining continues to later rows. This is deliberate poison-batch handling: inspect the `batch_id` with `run --get-batch UUID`, repair the cause, and send corrected data with a new batch ID. A full buffer returns **6**. Exit codes: **0** drained, **1** terminal/local error, **2** configuration/CLI error, **3** corrupt outbox, **4** credential/source fault, **5** retry limit reached, **6** buffer full. Each invocation emits one JSON summary line, without credentials.

Mock-transport tests cover buffering, retries, ordering, key faults, and keepalives. Real-Core outage, crash-after-commit, rate-limit, and key-rotation checks require the isolated staging stack. They were **NOT TESTED** in the current environment.
