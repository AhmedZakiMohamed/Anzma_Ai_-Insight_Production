# Changelog

All notable changes to Anzma AI Insight Core are recorded here. This file is maintained for the proposed v0.1 release; no tag has been created.

## [0.1.0] — proposed, untagged

### Added

- Versioned Core ingestion at `POST /api/v1/ingest/batches` with per-record results, exact batch replay recognition, rejection audit, immutable telemetry conflicts, source-scoped external asset mapping, per-source hashed API keys, and the documented v1 contract.
- Additive migrations 006–008 and an advisory-locked migration runner for existing databases.
- Separate named automated tests for each of the 16 ingestion contract conformance vectors.
- Clean-clone Compose deployment and first-batch instructions, configurable host ports, and `/health` / `/ready` verification steps.
- Functional, migration, deployment, data-validation, and known-issues evidence for Phase 0.

### Fixed

- `scripts/apply_migrations.py --help` now displays usage without requiring a database connection.
- Dashboard smoke query now uses the latest supported 31-day interval, even when test data contains a much older legacy timestamp.
- The CRAC recovered-history test now uses an isolated fixture and separately checks active-alert status, removing its test-order assumption.
- Compose host ports can be changed with `DB_HOST_PORT`, `API_HOST_PORT`, and `OLLAMA_HOST_PORT` to avoid local port collisions.
- Deployment docs now describe migrations 006–008 for existing volumes, service/readiness checks, and source/key/mapping provisioning.

### Compatibility

- Legacy `POST /events` and `POST /alerts` remain available and passed the integration suite.
- Existing H0 telemetry/event/alert/incident and catalogue rows remained unchanged in the disposable migration rehearsal. Legacy quality/provenance fields remain NULL where their historical values are unknown.

### Verification limits

- No shared/live database migration, push, or tag was performed.
- General REST/Socket.IO authentication, production deployment security, successful live Ollama inference, and a real device connector remain outstanding; details are in [`KNOWN_ISSUES.md`](KNOWN_ISSUES.md) and [`docs/RELEASE_CHECKLIST_v0.1.md`](docs/RELEASE_CHECKLIST_v0.1.md).
