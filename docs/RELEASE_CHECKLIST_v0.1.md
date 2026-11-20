# v0.1 release checklist

**Status date:** 19 November 2026  
**Proposed tag:** `v0.1.0` (not created)  
**Base HEAD:** `8cf0bf22a7652254cae95a506a7b067e25a73e8f` (worktree contains uncommitted Phase 0 closure changes; exact tested final tree must be recorded after verification)
**Release decision:** local worktree verification recorded; release artifact not approved because the closure tree is uncommitted and live model/browser verification remain incomplete.

| Gate | Status: PASS / FAIL / NOT TESTED | Evidence / detail |
|---|---|---|
| Source branch and current worktree identified | PASS | `feature/ingestion-foundation`, base HEAD `8cf0bf2`; no commit, push, or tag per instruction. |
| Clean-clone install of the final closure tree | NOT TESTED | Current closure changes are uncommitted and therefore not represented by a cloneable commit. An earlier clean clone at `07bf77e` passed before these closure changes. |
| Fresh Compose install from the current worktree | PASS | New project/volume `anzma-phase0-closure`; DB and API healthy; first authenticated batch accepted. |
| PostgreSQL/Ollama loopback defaults | PASS | Resolved Compose config and running containers showed `127.0.0.1:15438` and `127.0.0.1:11436`. |
| PostgreSQL readiness | PASS | `pg_isready` returned “accepting connections”. |
| API liveness/readiness and intentional public routes | PASS | `/health`, `/ready`, dashboard/static files and `/docs` work unauthenticated. |
| Fresh install applies schema and migrations 003–008 | PASS | Clean-install ledger recorded 006, 007, 008. |
| Existing H0 migration rehearsal on isolated restored DB | PASS | 006–008 applied once, second run skipped all, all 13 pre-existing table counts preserved, `verify_schema.sql` returned OVERALL OK; see [`DAY3_MIGRATION_REHEARSAL.md`](DAY3_MIGRATION_REHEARSAL.md). |
| Shared/live database migration | NOT TESTED | Deliberately not performed. |
| Legacy rows remain unchanged; NULL quality stays visible | PASS | 4,669 legacy telemetry rows retained in `v_telemetry_valid`; 955 legacy events remain source-less in disposable rehearsal. |
| Ingestion security and audit path | PASS | Hashed per-source key, mapping, accepted batch, replay, partial rejection, conflict immutability, and legacy POST endpoints verified on disposable DB. |
| Normal REST and Socket.IO authentication | PASS | Missing/invalid/valid bearer, cookie session/logout, and network socket handshakes passed on the isolated stack. Token is shared per deployment; no per-user RBAC. |
| 16 contract vectors have individual test names/assertions | PASS | All 16 have distinct named assertions; frontend/API suite passed 38/38 on the final worktree. |
| Operational regression and configuration test suites | PASS | Unit 28/28, integrity 23/23, Day 2 43/43, chat 31/31, frontend/API 38/38, snapshot comparator 3/3, deployment config 4/4 = 170/170. |
| Dashboard smoke default range | PASS | Uses bounded latest 31-day window; smoke returned 12 monitored assets even with a 2001 legacy event present. |
| API snapshot comparison | PASS | All three H0 post-migration files had exact matching hashes; comparator returned zero differences. `1e-12` tolerances are tested and a `0.01 C` change fails. |
| Successful live Ollama/Qwen inference | NOT TESTED | Isolated pull reached 99% then failed on a registry request with `dial tcp 104.18.17.170:443: i/o timeout`; follow-up `ollama list` was empty. No live explanation was run. |
| Ollama-unavailable deterministic fallback | PASS | Forced unreachable endpoint; response stated model unavailable and used deterministic stored-evidence explanation. |
| Real vendor connector and simulator connector | DEFERRED TO PHASE 1 | Neither is implemented; first batch was manually submitted. |
| Visual browser acceptance | NOT TESTED | API/route smoke tests do not establish visual rendering. |
| Production backup/restore | NOT TESTED | H0 dump restore was a disposable rehearsal; no production restore was exercised. |
| Freshness/gap job and retention policy | DEFERRED TO PHASE 1 | No scheduled freshness job or retention policy exists. |
| Single-replica deployment assumption documented | PASS | [`KNOWN_ISSUES.md`](../KNOWN_ISSUES.md) records the single-process limitation. |

## Tag proposal

Proposed name: `v0.1.0`. No tag or commit was created. The current worktree is not a release commit. Live model inference, browser visual acceptance, and a clean clone of the final uncommitted tree remain NOT TESTED; vendor connectors and freshness/retention work are DEFERRED TO PHASE 1. Shared/live DB migration and production restore remain NOT TESTED.
