# v0.1 release checklist

**Status date:** 19 November 2026  
**Proposed tag:** `v0.1.0` (not created)  
**Proposed code target:** `a9de778cf0793a530ae6e347480f07820c17ab7d`  
**Release decision:** not approved. This checklist records evidence and blockers; it does not authorize a tag.

| Gate | Status: PASS / FAIL / NOT TESTED | Evidence / detail |
|---|---|---|
| Source branch and commits are local and reviewed | PASS | `feature/ingestion-foundation`; code commit `a9de778`; no push or tag. |
| Clean-clone install with fresh PostgreSQL volume | PASS | [`DAY3_DEPLOYMENT_VERIFICATION.md`](DAY3_DEPLOYMENT_VERIFICATION.md); PostgreSQL 16 and API became healthy. |
| Port override behavior for co-located stacks | PASS | Clean clone used host ports 15437, 18006, 11435. |
| PostgreSQL readiness | PASS | `pg_isready` returned “accepting connections”. |
| API liveness and DB readiness | PASS | `/health` and `/ready` both returned HTTP 200 with their documented JSON bodies. |
| Fresh install applies schema and migrations 003–008 | PASS | Clean-install ledger recorded 006, 007, 008. |
| Existing H0 migration rehearsal on isolated restored DB | PASS | 006–008 applied once, rerun was a no-op, counts/history preserved; see [`DAY3_MIGRATION_REHEARSAL.md`](DAY3_MIGRATION_REHEARSAL.md). |
| Shared/live database migration | NOT TESTED | Deliberately not performed. |
| Legacy rows remain unchanged; NULL quality stays visible | PASS | 4,669 legacy telemetry rows retained in `v_telemetry_valid`; 955 legacy events remain source-less in disposable rehearsal. |
| Ingestion security and audit path | PASS | Hashed per-source key, mapping, accepted batch, replay, partial rejection, conflict immutability, and legacy POST endpoints verified on disposable DB. |
| 16 contract vectors have individual test names/assertions | PASS | All vectors separately named; three fresh-stack runs each passed 37/37 frontend/API checks. |
| Operational intelligence regression suites | PASS | Unit 28/28, integrity 23/23, Day 2 43/43, chat 31/31, frontend/API 37/37, E2E fallback 15/15. |
| Dashboard smoke default range | PASS | Uses bounded latest 31-day window; smoke returned 12 monitored assets even with a 2001 legacy event present. |
| API snapshot exact numeric equality | FAIL | One final-digit `avg_zone_temp` delta: 17.872061855670097 vs 17.8720618556701; alerts/incidents byte-identical. Decide comparison tolerance/representation before claiming byte-identical parity. |
| REST and Socket.IO authentication | FAIL | Not implemented outside ingestion; ISS-015. |
| Default PostgreSQL/Ollama host network exposure | FAIL | Compose binds host ports on all interfaces by default. Use firewall/network restrictions; release hardening remains open. |
| Successful live Ollama/Qwen inference | NOT TESTED | The unavailable-Ollama fallback path passed 15/15; live response path is not evidenced in this run. |
| Ollama-unavailable deterministic fallback | PASS | Forced unreachable endpoint; response stated model unavailable and used deterministic stored-evidence explanation. |
| Real vendor connector and simulator connector | NOT TESTED | Neither is implemented; first batch was manually submitted. |
| Visual browser acceptance | NOT TESTED | API/route smoke tests do not establish visual rendering. |
| Production backup/restore and retention policy | NOT TESTED | H0 dump restore was disposable rehearsal only; no production procedure was exercised. |
| Single-replica deployment assumption documented | PASS | [`KNOWN_ISSUES.md`](../KNOWN_ISSUES.md) records the single-process limitation. |

## Tag proposal

Proposed name: `v0.1.0`. Candidate code commit: `a9de778cf0793a530ae6e347480f07820c17ab7d`. No tag was created. The checklist contains release blockers (unauthenticated REST/Socket.IO and exposed default ports) and an unresolved exact-float snapshot delta; an authorized release owner should resolve or explicitly accept those before creating a tag.
