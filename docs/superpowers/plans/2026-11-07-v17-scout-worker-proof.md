# V17 Scout Worker-Proof Reconciliation Implementation Plan

> **For agentic workers:** Execute inline with superpowers:executing-plans and superpowers:test-driven-development. Each implementation step is test-first and must be verified on the isolated branch.

**Goal:** Correct false-negative terminal acceptance when PostgREST truncates the Scout state-event read at 1,000 rows, while retaining durable job receipts as independent worker proof.

**Architecture:** Keep the existing Render/Supabase durable Scout queue, its single in-process worker, and the GitHub Actions OIDC scheduler as the only production orchestration path. Derive worker-path evidence from durable job attempt/receipt fields while separately reporting missing state-event transitions. Trigger.dev remains a DEV-only experiment; no second production scheduler or worker is introduced.

**Tech Stack:** Python 3.11, pytest, existing Supabase/PostgREST queue schema, GitHub Actions.

**Spec:** `CLAUDE.md` sections 2–9; observed run `42388793530` and artifact `13581298182`; current queue contract in `artifacts/wow-engine/migrations/20261003_v17_scout_handoff_queue.sql`.

## Global Constraints

- `can_execute=false` and `WOW_DRY_RUN_ONLY=true` remain mandatory.
- The V17 Terminal Reducer remains terminal authority.
- No model, probability, calibration, certification, scoring, or publication behavior changes.
- No production DB mutation, Render deploy, queue-worker replacement, or scheduler change.
- Do not weaken the append-only state-event contract; missing events remain visible as warnings.
- Never count queued/unclaimed rows as worker-processed.

## Review Focus

- A job with `attempt_count=0` must never prove worker execution, even if a malformed row claims a terminal state.
- A worker failure ending in `HANDOFF_BLOCKED` after a real claim must count as a worker attempt, but not as a successful model evaluation.
- A persisted specialist receipt must be structurally valid and preserve `can_execute=false` at receipt, result, and outcome levels.
- Missing worker transition events must remain visible and must not be silently backfilled with fabricated historical timestamps.
- Objective fan-out creates separate queue jobs; counts must be job-scoped, not accidentally collapsed by candidate ID.

---

### Task 1: Derive worker proof from durable job rows

**Files:**
- Modify: `artifacts/wow-engine/v17/scout_handoff_queue.py`
- Test: `artifacts/wow-engine/test_v17_scout_handoff_queue.py`

**Interfaces:**
- Paginate job and event reads in stable order; derive job-scoped worker evidence from attempt counters/receipts and compare it with the fully paginated event ledger.
- `read_run_summary(db, source_run_id, include_receipts=False)` exposes `specialist_processing_seen`, `model_evaluated`, `v17_qualified`, and explicit worker-event-gap counts.

- [ ] Write a regression test for more than 1,000 state events where the worker transitions are on page 2; expect complete lifecycle counts and no false event-gap warning.
- [ ] Write a regression test for terminal `MODEL_EVALUATED` jobs with `attempt_count=1` and valid specialist receipts but no worker events; expect worker proof from durable job rows plus explicit event gaps.
- [ ] Write boundary tests proving attempt_count 0 and malformed/governance-invalid receipts do not prove worker execution.
- [ ] Run the focused tests and confirm they fail for the missing derived evidence.
- [ ] Implement bounded `.range(start, start + page_size - 1)` pagination for job and event reads, ordered by stable unique keys; retain durable job-row evidence and expose genuine event gaps.
- [ ] Re-run focused tests and the complete Scout queue unit tests.

### Task 2: Preserve the evidence distinction in terminal acceptance

**Files:**
- Modify: `artifacts/wow-engine/v17/scout_handoff_terminal_acceptance.py`
- Test: `artifacts/wow-engine/test_v17_scout_handoff_terminal_acceptance.py`

**Interfaces:**
- `validate_terminal_summary(summary)` continues to require terminal row accounting, no held rows, and non-executable governance.
- Durable job proof can satisfy `specialist_processing_seen`; missing event transitions are returned as explicit warnings, not silently treated as present.

- [ ] Add a regression test using the 622-row failure shape: all jobs terminal, attempt_count 1, valid non-executable specialist receipts, 0 worker transition events, and fully reconciled row accounting. Expect worker-path acceptance plus a warning for event gaps.
- [ ] Add a negative test where no durable attempt/receipt proof exists; expect `SCOUT_TERMINAL_ACCEPTANCE_WORKER_PATH_NOT_PROVEN`.
- [ ] Run focused tests and confirm the positive case fails before implementation.
- [ ] Implement explicit warnings and preserve the current PASS/HOLD rules for row accounting and governance.
- [ ] Re-run terminal acceptance tests.

### Task 3: Verify and document Trigger.dev production fit

**Files:**
- Create: `docs/wow/architecture/TRIGGER_DEV_V17_PRODUCTION_ADOPTION_ASSESSMENT.md`
- Modify: `integrations/trigger-dev-pilot/SECURITY_REVIEW.md` only if the existing pilot branch is separately updated; do not merge it into this fix branch.

- [ ] Record that the existing production queue/worker and GitHub OIDC scheduler already provide durable claim/lease/retry and terminal reconciliation.
- [ ] Record that the observed run processed 622 jobs but returned zero qualified rows, with exact per-sport model blockers.
- [ ] Recommend no production Trigger.dev worker/scheduler until a measured gap remains after fixing the evidence path; keep the existing DEV pilot isolated.
- [ ] State that Render workspace and production Supabase project identity must be confirmed before live runtime/schema verification.

### Task 4: Full verification and review

**Files:**
- No additional source files unless a test exposes a necessary contract correction.

- [ ] Run the focused Scout queue, terminal acceptance, migration-contract, and Postgres integration tests (Postgres test remains conditional on ephemeral CI DB configuration).
- [ ] Run the repository's full `wow-engine-verify` workflow and `wow-v17-nightly-multiscout` contract tests on the exact head.
- [ ] Review the full branch diff against baseline `e55999b822c0cd3f132ba61292e7b51fb6c30d97`.
- [ ] Open a draft PR; do not merge or deploy.
- [ ] Record any remaining runtime/schema proof requirement separately.

