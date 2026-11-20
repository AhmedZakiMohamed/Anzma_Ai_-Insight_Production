# Reference simulator connector

This standalone package sends deterministic demo telemetry and events to Anzma Core over the versioned HTTP ingestion contract. It has no Core database access and imports no Core application code. Provision a source, building code, and source-scoped asset mappings separately with `scripts/ingestion_admin.py`.

## Run

From the repository root:

```sh
PYTHONPATH=connectors/reference_simulator python -m simulator.cli run --scenario normal --batches 1
```

Required settings are `ANZMA_SIMULATOR_CORE_URL`, `ANZMA_SIMULATOR_SOURCE_CODE`, `ANZMA_SIMULATOR_BUILDING_CODE`, and exactly one of `ANZMA_SIMULATOR_API_KEY` or `ANZMA_SIMULATOR_API_KEY_FILE`. `ANZMA_SIMULATOR_SEED_BUILDING` selects `main-tower` or `data-center-annex` from the seed catalogue and must match the provisioned source's building. A non-secret TOML file may be selected with `ANZMA_SIMULATOR_CONFIG_FILE`; environment values override its settings. The key is never stored with batches or printed. State defaults to `~/.anzma-simulator/outbox.sqlite3` in a private directory.

The one-shot scenarios are `normal`, `fault`, `stale-silent`, `bad-record`, `late-out-of-order`, and `duplicate-batch`. `--keepalive` sends an empty records batch. `--get-batch UUID` fetches the stored Core outcome. The Dockerfile runs the same module as a non-root user; mount `/var/lib/simulator` as a private persistent directory and supply secrets at runtime.

This is D2's minimal transactional batch ledger. Capacity enforcement, overflow handling, retry/backoff, FIFO outage draining, and recovery policy are intentionally deferred to D3; a transient response leaves its batch pending and exits without a retry loop.
