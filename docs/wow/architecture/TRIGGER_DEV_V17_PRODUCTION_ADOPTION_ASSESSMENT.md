# Trigger.dev and WOW V17 — Production Adoption Assessment

**Assessment date:** 2026-11-08 UTC
**Decision:** `NO_GO_FOR_PRODUCTION_WORKER_OR_SCHEDULER`
**Current Trigger.dev pilot:** Development-only, read-only/synthetic
**Production authority:** Existing Render/Supabase queue, GitHub Actions OIDC, V17 specialists and V17 Terminal Reducer

## Executive decision

Do not add Trigger.dev as a production scoring worker or scheduler at this time. WOW already has a durable Supabase Scout handoff queue, row-leased worker execution in the Render FastAPI process, GitHub Actions OIDC scheduling, retry/lease rules, specialist receipts, and terminal reconciliation.

The latest inspected production run proves the existing worker did execute all 622 handed-off jobs. The failure was in terminal evidence reconciliation: the job rows contain attempts and specialist receipts, while the separate state-event ledger is missing the worker-transition events that the summary currently requires. Trigger.dev retries cannot repair that data-model/evidence mismatch.

The model blockers are independent of orchestration. The same run returned zero qualified rows because the sport-specific model bridges, artifacts, certifications, event identity and feature inputs were not ready. Adding another task platform would not make those models available or qualified.

Keep PR #2507 and the Trigger.dev project in Development for synthetic retry/idempotency experiments. Do not create a production Trigger.dev deployment, schedule, worker, or production credential.

## Existing production architecture

1. `CLAUDE.md` identifies `artifacts/wow-engine/api_ncaaf_acceptance:app` on Render as the production FastAPI runtime and Supabase as governed persistence.
2. `render.yaml` defines `wow-governed-probability-engine` as the production web service. Its declared configuration enables `WOW_SCOUT_ASYNC_HANDOFF_ENABLED=true` and bounds `WOW_SCOUT_HANDOFF_WORKERS=1`.
3. `artifacts/wow-engine/v17/scout_handoff_queue.py` claims jobs through the Supabase `wow_claim_scout_handoff_job` RPC, processes the existing canonical specialist route, and persists specialist receipts using the finish/retry/block RPCs.
4. `artifacts/wow-engine/migrations/20261003_v17_scout_handoff_queue.sql` uses row locking with `FOR UPDATE SKIP LOCKED`, bounded leases, retry restrictions, and an append-only lifecycle event table.
5. `.github/workflows/wow-v17-nightly-multiscout.yml` is already the scheduled discovery/auto-advance path. It runs daily at 10:00 UTC, also runs after the governed Render deploy workflow, and uses GitHub Actions OIDC for protected backend operations.
6. `render.yaml` explicitly keeps `WOW_PROP_LIFECYCLE_AUTOPILOT_ENABLED=0` because the protected-main GitHub OIDC workflow is the single scheduler. It also labels the proposed persistent Key Value and separate Celery worker topology as definition-only pending explicit approval; those are not the Scout queue worker.

This means a production Trigger.dev scheduler or queue consumer would overlap an existing scheduler/worker boundary. A future migration would have to replace one owner, not add another.

## Observed end-to-end run

Source: [Nightly Multi-Scout run #42388793530](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/actions/runs/42388793530), downloaded artifact 13581298182.

The durable run artifact contained:

- 622 queue jobs.
- 622/622 jobs with `attempt_count=1`.
- 622/622 jobs in `MODEL_EVALUATED`.
- 622/622 jobs with a persisted specialist receipt and one specialist result row.
- The state-event ledger contained 250 each of `DISCOVERED`, `RESEARCH_INTEREST`, `RED_TEAM_PASSED`, and `SPECIALIST_HANDOFF_QUEUED`, but zero `SPECIALIST_PROCESSING` and zero `MODEL_EVALUATED` events.

The old `read_run_summary` counted `specialist_processing_seen` and `model_evaluated` only from the event table. It therefore reported `SCOUT_TERMINAL_ACCEPTANCE_WORKER_PATH_NOT_PROVEN` despite the durable job rows proving that the queue worker claimed and evaluated every job.

The repair in [PR #2517](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2517) derives worker evidence from the job attempt counter and valid non-executable specialist receipt, while separately exposing missing event transitions as warnings. It does not change model output or qualification.

## Actual model blockers in the same run

All 622 jobs were terminally accounted for, but zero produced a qualified pick:

| Sport | Rows | Observed blocker |
|---|---:|---|
| NBA | 56 | `MODEL_UNAVAILABLE`: team-event bridge not registered; certification not active |
| NCAAF | 272 | `MODEL_UNAVAILABLE`: team-event bridge not registered; certification not active |
| NCAAB | 170 | `MODEL_UNAVAILABLE`: team-event bridge not registered; certification not active |
| NHL | 72 | `MODEL_INPUTS_INSUFFICIENT`: model artifact not resolvable; certification not active; required inputs insufficient |
| NFL | 26 | `MODEL_INPUTS_INSUFFICIENT`: provider event identity incomplete |
| NFL | 26 | `LLP_EVENT_GOVERNANCE_NOT_PROVEN`: forward certification unproven |

These are fitted-model, certification, identity, and input-evidence problems. Trigger.dev can retry a transient request, but cannot register a missing bridge, manufacture a fitted artifact, certify a model, resolve an official event identity, or create missing historical features.

## What Trigger.dev can safely contribute today

- Exercise provider-managed retries with synthetic, non-production tasks.
- Test sequential duplicate submission/idempotency behavior.
- Provide run-level traces for isolated development experiments.
- Serve as a future candidate for replacing an existing scheduler/worker only if production measurements show a concrete bottleneck.

The existing DEV pilot's synthetic retry and duplicate-key receipts are not evidence of production model scoring. The prior live receipts also predate the newest provider-attested Development guard and need a post-guard retest before the pilot itself is considered fully accepted.

## Reconsider production adoption only if all are true

1. A measured backlog, worker saturation, memory/latency limit, or retry-recovery gap remains after fixing the existing queue evidence path.
2. A design replaces a single existing scheduler or worker; it does not create a second active consumer or overlapping schedule.
3. Trigger.dev uses a narrowly scoped credential and allowlisted route/RPC surface; do not copy the broad `WOW_ACTION_API_KEY` or Supabase service-role credential into Trigger.dev.
4. The existing Supabase row lease and terminal receipts remain the source of truth. Test concurrent duplicate submissions, lease expiry, retry exhaustion, cancellation, timeouts, provider outage, and recovery.
5. Exact-head CI, post-deployment live acceptance, non-author SIRT approval, and separate Release Authority approval pass.
6. Cost/plan limits are verified from actual task usage. Do not upgrade or incur paid retries without explicit approval.

## Safety and authority

- `can_execute=false`; `WOW_DRY_RUN_ONLY=true`.
- V17 fitted models retain probability authority.
- The V17 Terminal Reducer remains terminal authority.
- No production scheduler, worker, deployment, schema, or credential is changed by this assessment.
- No production adoption is authorized by this document.
