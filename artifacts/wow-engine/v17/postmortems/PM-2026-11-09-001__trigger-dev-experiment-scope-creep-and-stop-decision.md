# PM-2026-11-09-001 — Trigger.dev Development experiment scope creep and stop decision

- status: CLOSED_SUPERSEDED
- severity: P3
- primary_subsystem: ENGINEERING_GOVERNANCE
- workflow_stage: DECISION_AND_CLOSEOUT
- current_owner: WOW_ENGINEERING (implementation) / SIRT (independent challenge)
- reported_by: SIRT
- reconstruction_utc: 2026-11-09T14:30:00Z
- risk_class: R0_DOCUMENTATION_ONLY
- runtime_generation: V17_ACTIVE
- terminal_authority: V17_TERMINAL_REDUCER
- decision: STOP_TRIGGERDEV_EXPANSION__CONTINUE_WOW_RESTORATION
- decision_status: RECOMMENDED_PENDING_OWNER_CONFIRMATION
- can_execute: false

## Summary

An isolated, read-only Trigger.dev DEVELOPMENT pilot (PR #2507) and a stacked
engineering-recovery-intake extension (PR #2529) were carried well past the
point where the pilot's stated question had been answered. The technical
capability was proven — cloud-hosted DEVELOPMENT task execution, bounded
retry, idempotency and durable checkpoint/resume — but the work expanded into
dependency patching, authentication troubleshooting, cloud worker hosting,
security review routing and release governance without ever demonstrating an
autonomous engineering repair or dispatch.

The SIRT recommendation is to stop expanding Trigger.dev, preserve the
Development evidence, and return Engineering to the existing WOW recovery
chain (Engineering Controller → Render/Celery worker → QA/Release/SIRT).
That architecture is already the controlling decision recorded in issue
#2364, which retired Trigger.dev as an inline production recovery dependency
on 2026-11-01.

This record is a decision and closeout postmortem. It authorizes no code
change, deployment, credential action or probability behavior.

## Impact

- User-visible impact: none. No production route, model, probability,
  settlement or publication behavior was touched; nothing was merged.
- Model/probability impact: none. No model invocation, calibration, terminal
  reducer or probability authority was involved.
- Governance impact: material process cost. Two draft PRs, two open SIRT
  intake issues, a patched third-party CLI, a GitHub-hosted DEVELOPMENT worker
  and a temporary credential path were created for an experiment that had
  already answered its own question.
- Data/persistence impact: none in production. No Supabase, Render or
  governed table write occurred.
- Evidence impact: the pilot's three successful DEVELOPMENT executions and the
  regression/security findings are worth preserving as isolated diagnostics.

## Detection

- Detected at: 2026-11-09 (postmortem review); underlying drift ran from
  2026-11-07 through 2026-11-09.
- Detected by: SIRT review of accumulated Trigger.dev work against the
  controlling architecture decision in issue #2364.
- First known bad run/request: not applicable — no incident-producing run
  exists. The process failure is cumulative, not a single failed execution.
- Last known good run/request: workflow run `42555938361`
  (`wow-trigger-remote-dev-acceptance`), conclusion `success`, head
  `6b38aa6de0191a2eb5f93049c1a446750b9478d9`.

## Evidence

Independently read back from GitHub at 2026-11-09T14:27Z–14:30Z:

- Run `42555938361` — workflow `wow-trigger-remote-dev-acceptance`,
  event `pull_request`, status `completed`, conclusion `success`, head SHA
  `6b38aa6de0191a2eb5f93049c1a446750b9478d9`, branch
  `feat/trigger-v17-engineering-recovery-intake-20261108`, created
  `2026-11-09T03:21:18Z`, updated `2026-11-09T12:49:29Z`, path
  `.github/workflows/wow-trigger-remote-dev-acceptance.yml`.
- PR #2507 — `open`, `draft: true`, `merged: false`, base `main`, head
  `85c1f68611004f61f62694e53bc01ce8d03bda87`, updated
  `2026-11-08T21:07:59Z`. Scope declared in the PR body is an isolated
  DEVELOPMENT-only read-only health pilot with no scheduler, scoring,
  settlement or release-authority change.
- PR #2529 — `open`, `draft: true`, `merged: false`, stacked base
  `feat/trigger-dev-readonly-pilot-20261107`, head
  `6b38aa6de0191a2eb5f93049c1a446750b9478d9`, updated
  `2026-11-09T03:21:13Z`. Its own body states it makes no LLM call and holds
  no GitHub write or dispatch credential.
- Issue #2364 — `closed`, `state_reason: not_planned`, closed
  `2026-11-01T13:50:55Z`, disposition `SUPERSEDED_UNMERGED`; inline integration
  PR #2359 closed without merge at `a88a2dea817038be07f8606226d05d4c28680d7c`;
  replacement architecture is the existing Render `wow-sirt-external-sentinel`
  cron plus existing `wow-agent-worker` Celery execution.
- Issue #2516 — `open`; independent SIRT review intake for PR #2507.
- Issue #2530 — `open`; independent SIRT review intake for PR #2529.

Preserved but not re-verified in this readback (cited from the pilot's own
documented receipts and must not be treated as independently accepted):
the three successful DEVELOPMENT task executions and their run receipts, the
28/41/48/51-test exact-head CI progression, and the local `minimatch`/Trigger
CLI compatibility patch.

## Root Cause

The experiment had no enforced stopping point. Once cloud-hosted DEVELOPMENT
execution succeeded, each remaining failure (CLI dependency incompatibility,
CLI personal-access-token authentication, Preview-plan restrictions, release
governance checks) was treated as the next problem to solve rather than as
evidence weighted against the platform's marginal value.

Root cause status: CONFIRMED (process), confidence HIGH.

Contributing factors:

1. **Success metric mismatch.** Green CI, completed synthetic tasks and
   checkpoint/resume were read as progress toward autonomous recovery, while
   the actual objective — an authorized real dispatch and repair — was never
   exercised. Measured outcome: 3/3 synthetic executions, 0 autonomous repairs.
2. **Architecture not re-consulted.** Issue #2364 had already retired
   Trigger.dev from the production recovery path and named Render/Celery plus
   Engineering authority as the execution chain. The pilot continued as if
   that decision were still open.
3. **Environment and plan mismatch.** A DEVELOPMENT-only guard, hosted
   Preview deployment and the plan's Preview-branch limits pulled the work
   toward a bespoke GitHub-hosted worker rather than the local CLI proof the
   pilot actually needed.
4. **Dependency and credential tax.** Patching an installed third-party CLI
   against `minimatch@11` and splitting Development API key, CLI PAT, GitHub
   secrets and approval workflow into separate troubleshooting stages consumed
   effort with no bearing on WOW recovery capability.
5. **Late release governance.** Draft status, missing canonical incident
   binding, stacked-PR base and stale review SHAs blocked release only after
   the implementation effort was already spent.
6. **Environment hygiene at intake.** Early commands were run outside the
   isolated project directory, producing unrelated audit findings and
   missing-script errors that did not test the pilot at all.

## V17 Classification

- BACKEND_RUNTIME: NONE — no production service, route or deploy changed.
- MODEL_CAPABILITY: NONE — no fitted model, scorer or calibration path invoked.
- REPOSITORY_GOVERNANCE: TRIGGERDEV_EXPERIMENT_STOP — experiment frozen at the
  Development proof-of-concept; production expansion refused.
- LIVE_GPT_EDITOR_SYNC: NONE.
- Terminal status: NOT_APPLICABLE — no model row was scored, held or rejected.
- `scoring_attempted`: false.

## Controlling Lane / Specialist

Not applicable. No probability lane or specialist is involved and
`probability_authority=NONE`. Scout/research-style workflow evidence must not
be read as model output, and nothing in this record substitutes for one.

## Failure Semantics

Not applicable — no model, scorer or provider call was made by this work, so
no `MODEL_UNAVAILABLE` / `MODEL_SCORER_FAILED` / `MODEL_INPUTS_INSUFFICIENT` /
`MODEL_OUTPUT_INVALID` disposition arises. The pilot's own typed transient and
permanent failure handling is preserved in PR #2529 and is not reclassified
here.

## Remediation

- Engineering fix ID(s): none. This is a decision record, not an
  implementation remediation; no `FIX-` record is claimed.
- Temporary mitigation: stop expansion; keep both PRs draft and unmerged.
- Permanent fix: execute WOW recovery through the existing Engineering
  Controller, Celery worker, writer lease/fencing and QA/Release/SIRT chain.

Go/no-go decision (2026-11-09):

- STOP — expanding Trigger.dev into production autonomous recovery. No further
  orchestration features, approvals, infrastructure or dependencies.
- KEEP — the DEVELOPMENT evidence, regression tests, security findings and
  reproducible setup.
- CONTINUE — the original autonomous recovery objective on existing services,
  proving one real authorized recovery lifecycle end to end.

Ordered closeout actions:

1. Declare the Trigger.dev Development experiment evidence-complete; do not
   describe the broader recovery system as complete.
2. Freeze PRs #2507 and #2529; the authorized owner closes them as superseded
   only if the changes are not needed in the approved architecture. Preserve
   all receipts and audit records.
3. Revoke temporary Trigger.dev credentials, subject to owner confirmation
   that no other authorized task depends on them.
4. Return Engineering to real controller dispatch, lease safety, worker
   execution and production readback.
5. Apply a go/no-go requirement before any future second execution platform:
   an independently measurable improvement in actual user or engineering
   outcomes that the existing controller cannot deliver.

## Verification

- Regression test(s): not applicable to this record.
- Acceptance test(s): not applicable — no code, route or model behavior is
  changed by filing this decision.
- Production verification: none claimed. No production deploy, route, model or
  persistence readback is asserted by this postmortem.
- Verified commit/deploy: none. This record is documentation only.

Open conditions that block an "everything closed" reading:

- SIRT intake #2516 (PR #2507) and #2530 (PR #2529) remain `open`; no
  non-author SIRT verdict exists for either head. The experiment's technical
  receipts may be declared evidence-complete, but independent review is not
  satisfied and must not be reported as such.
- Closeout actions 2 and 3 (PR freeze/closure, credential revocation) require
  repository-owner and credential-owner authorization and are not performed by
  this record.

## Prevention / Follow-up

- Adopt an explicit stopping rule: when the stated question of an experiment
  has been answered, further work requires a new, separately justified
  objective rather than another patch cycle.
- Require that the success metric name the real outcome (authorized dispatch,
  verified repair, production readback) instead of workflow color or synthetic
  task completion.
- Treat an existing architecture decision as binding input to new integration
  work; reopening it requires an explicit decision, not incremental commits.
- Keep every diagnostic pilot outside the production execution chain, with its
  own credentials, and record a time-boxed revocation path before starting.

## Closure

- Closed at: 2026-11-09T14:30:00Z (decision recorded)
- Closed by: SIRT recommendation; owner confirmation pending for closeout
  actions 2 and 3.
- Final status: `EXPERIMENT_EVIDENCE_COMPLETE__EXPANSION_STOPPED`
- Linked engineering fix(es): none. Superseding architecture: issue #2364
  (existing Render/Celery execution chain). Related draft PRs: #2507, #2529.
  Open independent reviews: #2516, #2530.

---

This record cannot authorize, route, modify, approve, or cancel a wager/order.
`can_execute=false` remains unconditional.
