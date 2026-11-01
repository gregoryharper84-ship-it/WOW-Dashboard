# WOW V17 — Trigger.dev P0 Recovery Sentinel (read-only acceptance)

**Objective:** `WOW-V17-TRIGGER-RECOVERY-001`  
**Owner:** AMRT for recovery; Betting Intelligence for model acceptance; SIRT for independent challenge.  
**Scope:** `BACKEND_RUNTIME` only. `can_execute=false` and `V17_TERMINAL_REDUCER` remain controlling.

## What is implemented

The Trigger.dev task `wow-v17-recovery-sentinel` makes **only two unauthenticated GETs** to the existing, fixed WOW backend `https://wow-governed-probability-engine.onrender.com`:

1. `/health` — process liveness.
2. `/health/ready` — database, queue and worker-registry parity. This endpoint returns HTTP 503 with a typed `detail` body when not ready.

It fails closed on timeouts, non-200 responses, malformed evidence, or any unexpected `can_execute=true`. Its output includes timestamp, stable incident grouping key, raw HTTP statuses, readiness components, owner, objective ID, and explicit `UNVERIFIED_NOT_PROBED` for both model capability and Action receipts. A passing infrastructure probe is **not** a Full Model result.

The task does **not** create GitHub issues, persist a Supabase incident, call the scorer, assign probabilities, dispatch an engineering writer, merge, deploy, schedule recurring checks, or authorize betting. `durable_incident_written=false` and `dispatch_attempted=false` are explicit in every result. No new provider secrets required for these two GET requests.

## Reproducible tests

From the Trigger.dev branch checkout:

```bash
node --experimental-strip-types --test tests/wow-v17-recovery-probe.test.mjs
npx trigger.dev@4.8.3 dev
```

In another Terminal (same checkout), with the dev worker still running:

```bash
npx trigger.dev@4.8.3 tasks trigger wow-v17-recovery-sentinel --payload '{}' --wait
```

Read the complete run output, not only the CLI terminal success message. Record the run ID, timestamp and typed receipt. If the backend is unready, a task can **complete technically** while returning `status=BLOCKED`; this is the expected diagnostic result, not permission to mark runtime healthy.

## Acceptance and activation gates

- **Source/tests:** reproducible tests pass; input/API behavior verified against actual deployed backend version.
- **Dev:** task registers, executes, returns one structurally valid receipt on a real production endpoint; original smoke test remains unchanged.
- **Independent reliability:** SIRT verifies a known healthy and unhealthy case, confirms correct blocker type, missing-model distinction, no writes, no secret exposure, and no duplicate writer.
- **Promotion:** regenerate `pnpm-lock.yaml`, run repository checks, and complete protected PR review/merge. Production Trigger.dev deployment must be verified separately by its own run receipt.
- **Not yet implemented:** durable Supabase incident with idempotent upsert, GitHub/AMRT handoff, provider failover, scheduled checks, Action/model scoring, end-to-end product recovery. Do not claim complete for those goals.

## Cost/operability decision

The sentinel is **manually triggered** for now. After real environment acceptance, Engineering may add a low-frequency schedule with strict concurrency/timeout settings and confirm cost/duplicate-task policy. Do not install a second resident supervisor or bypass the existing protected-main engineering writer fence.
