# WOW Agent Runtime V1 — Staging Validation

Date: 2026-08-29
Scope: non-production validation only

## Governance

- can_execute=false
- DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true
- No production Supabase migration applied.
- No production Render service changed.
- No Render Key Value or worker resource provisioned.

## Supabase isolated staging migration

Status: PASS

The additive `agent_runtime_v1/migration.sql` was applied to an isolated Supabase project that contained no pre-existing `wow` schema and zero `public.wow_*` tables.

Verified after migration:

- private `wow` schema created successfully;
- 10 orchestration tables exist: `runs`, `run_candidates`, `worker_registry`, `agent_jobs`, `evidence_snapshots`, `agent_outputs`, `model_artifacts`, `capability_registry`, `terminal_decisions`, `audit_events`;
- `anon` has no `USAGE` privilege on schema `wow`;
- `authenticated` has no `USAGE` privilege on schema `wow`;
- `anon` and `authenticated` have no table grants in schema `wow`;
- no functions exist in schema `wow`, therefore the runtime migration introduced no `SECURITY DEFINER` functions;
- Postgres rejects `can_execute=true` at the `wow.runs` constraint;
- Postgres rejects `dry_run_only=false` at the `wow.runs` constraint;
- duplicate `agent_jobs.idempotency_key` is rejected;
- duplicate `agent_outputs.job_id` is rejected;
- run -> candidate -> evidence -> job -> output foreign-key lifecycle completed successfully in an integration fixture and was cleaned up afterward.

## Supabase security advisor

Runtime-schema finding: PASS / no new WOW Agent Runtime security-advisor finding identified.

The isolated project reports pre-existing warnings for `public.rls_auto_enable()` being a publicly executable `SECURITY DEFINER` function. That object is outside the new `wow` schema and was not created or modified by this migration. It remains a separate project-level remediation item and must not be misattributed to Agent Runtime V1.

## GitHub CI

`wow-engine-verify` initially failed because its legacy Render assertion required exactly one service. All governed backend tests in that run passed (153 passed); only the topology assertion failed. The workflow was updated to validate the intended three-resource Blueprint explicitly: Key Value, web API, and worker.

Subsequent CI remains authoritative for the current PR head. Do not promote GitHub CI to PASS until both `wow-engine-verify` and `wow-verify` complete successfully on the same current head SHA.

## Render staging

Status: BLOCKED_NOT_PROVISIONED

Current workspace inventory contains no Key Value instance and no Agent Runtime worker service. The existing governed probability engine remains on `main` with auto-deploy disabled.

The repository Blueprint defines:

- `wow-jobs`: persistent starter Key Value, `noeviction`;
- `wow-governed-probability-engine`: API using `agent_runtime_entrypoint:app`;
- `wow-agent-worker`: starter Celery worker, concurrency 2;
- `autoDeployTrigger: off` for API and worker;
- `WOW_CAN_EXECUTE=false` and `WOW_DRY_RUN_ONLY=true` on runtime services.

A real persistent-queue restart/duplicate-delivery test requires provisioning paid Render resources. This validation did not create paid infrastructure implicitly.

## Fitted-model lane

Status: MODEL_UNAVAILABLE

No certified server-owned fitted artifact + eligible calibrator has been attached to the Agent Runtime capability registry. The controlling-model worker rejects caller-supplied probability substitutes and returns a non-publishable blocker until the genuine provider is wired.

## Current certification ceiling

`SPECIALIST_CONTRACTS_PRESENT`

`PERSISTENT_MULTI_AGENT_RUNTIME_NOT_YET_CERTIFIED`

Remaining mandatory evidence before persistent-runtime certification:

1. current-head GitHub CI fully green;
2. provisioned persistent `noeviction` queue and worker;
3. worker restart / duplicate-delivery / timeout acceptance on real staging infrastructure;
4. real server-owned fitted-model artifact and calibrator for the first governed lane;
5. prospective shadow grading and calibration-health evidence;
6. explicit production approval before any production migration or deployment.
