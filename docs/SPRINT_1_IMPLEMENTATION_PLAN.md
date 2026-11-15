# Proposed Sprint 1 Implementation Plan

**Objective:** Deliver a freshly installable, configured Core by Friday while preserving existing behavior and recording reproducible verification results.
**Scope:** Core only; no Milestone project, vendor connector projects, real building integration, equipment control, model training, or additional model providers.

## 1. Ownership

- **Amr — Platform Integration & Agent Orchestration:** application boundaries, API/runtime integration, configuration, deployment integration, model-provider orchestration, dashboard/API consistency, and integration verification.
- **Ahmed — Data Intelligence & AI Reliability:** canonical data model and migration authority, KPI/evidence integrity, freshness/quality semantics, deterministic operational logic, and data-quality review.
- **Joint:** API/data contract review, external identity mapping, release acceptance, and demo readiness.

Ownership is proposed and must be confirmed with Ahmed. Schema, normalized observations, KPI, freshness, evidence, and rule changes depend on his review.

## 2. Proposed tasks

| ID | Task | Proposed owner | Dependencies | Completion evidence |
|---|---|---|---|---|
| S1-01 | Reproduce and record current baseline | Amr; Ahmed reviews data results | Agreed environment/branch | Commit, commands, outputs, and status for dashboard, KPIs, alerts/incidents, evidence, assistant/fallback |
| S1-02 | Confirm canonical schema and migration authority | Ahmed lead; Amr integrates deployment | S1-01 | Written schema authority and migration plan; deployment/tests use agreed source |
| S1-03 | Define application configuration contract | Amr lead; Ahmed reviews data settings | S1-01 | Settings inventory, consistent defaults, safe secret handling, startup validation |
| S1-04 | Confirm Core boundaries and preserve entry point | Amr lead; Ahmed reviews domain boundary | S1-01 | Agreed target structure and incremental extraction; application remains launchable |
| S1-05 | Reconcile API documentation | Amr lead | S1-01 | Documentation matches runtime routes/OpenAPI; future ingestion marked proposed |
| S1-06 | Introduce shared DB access boundary incrementally | Amr lead; Ahmed reviews data semantics | S1-02, S1-04 | Shared config/connection handling and targeted repository/service extraction; behavior checked |
| S1-07 | Standardize install and startup | Amr lead; Ahmed reviews migration/data startup | S1-02, S1-03 | Fresh-install instructions, DB readiness, migrations, persistent storage, restart verification |
| S1-08 | Verify health and dependency behavior | Amr lead | S1-03, S1-07 | Liveness/readiness exercised; DB/model outage behavior documented |
| S1-09 | Verify integrated Ollama/Qwen and fallback | Amr lead; Ahmed reviews grounding | S1-01, S1-03, S1-07 | Captured successful integrated request and model-unavailable fallback result |
| S1-10 | Run regression and negative scenarios | Amr lead; Ahmed owns data-integrity review | S1-06 through S1-09 | Outputs for core paths, missing/stale data, duplicate event, invalid config, unavailable DB/model |
| S1-11 | Prepare Friday demo and handover | Joint | S1-10 | Fresh-install runbook, demo/fallback paths, test evidence, limitations, release commit |

## 3. Suggested sequence

1. **Facts and decisions:** S1-01 through S1-03. Baseline and Ahmed's schema/config decisions guide safe changes.
2. **Contracts and boundaries:** S1-04 through S1-06. Preserve current API behavior while separating responsibilities; do not build connector projects.
3. **Repeatable deployment:** S1-07 and S1-08. Verify clean installation and restart.
4. **AI and release evidence:** S1-09 through S1-11. Capture real integrated model/fallback outputs and regression results.

Parallel work is appropriate only when tasks do not change the same schema or runtime path. Schema, KPI, freshness, evidence, and rule work depends on Ahmed's review.

## 4. Friday acceptance checklist

- [ ] Clean installation completes using documented steps.
- [ ] Environment settings require no customer-specific source edits.
- [ ] Fresh database migrations work.
- [ ] Data persists through container restart.
- [ ] Dashboard and system monitoring are demonstrated.
- [ ] Operational KPI behavior is demonstrated.
- [ ] Alert and incident processing is demonstrated.
- [ ] Evidence retrieval is demonstrated.
- [ ] Deterministic assistant fallback is demonstrated.
- [ ] Integrated Ollama/Qwen request succeeds with output recorded.
- [ ] Model-unavailable behavior is exercised and recorded.
- [ ] Actual test commands and outputs are documented.
- [ ] Known failures and incomplete items are listed honestly.
- [ ] Ahmed reviews canonical data, KPI, evidence, freshness, and rule changes.
- [ ] Milestone and connector implementations remain outside Core.
- [ ] No real building integration or equipment control is included.

## 5. Release risk

A Friday release is realistic only if a DB-backed test environment and canonical data-model decision are available early. Main risks are unavailable Docker/PostgreSQL, unresolved schema/migration ownership, inconsistent configuration, insufficient regression time, and failure to verify integrated Ollama/Qwen and fallback.

If an acceptance item is not demonstrated by Friday, mark it incomplete and state what evidence is still needed. Passing subsystem tests alone does not verify the complete Core.
