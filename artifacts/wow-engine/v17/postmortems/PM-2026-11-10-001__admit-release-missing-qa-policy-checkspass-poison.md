# PM-2026-11-10-001 — Main-scoped admit-release poisons checksPass when QA policy is absent

- status: FIX_IN_PROGRESS
- severity: P0
- primary_subsystem: DEPLOYMENT_RUNTIME
- secondary_subsystems: [WOW_HOST_ORCHESTRATION]
- workflow_stage: ENGINEERING
- current_owner: ENGINEERING_AGENT
- created_utc: 2026-11-10T15:45:00Z
- runtime_generation: V17_ACTIVE
- terminal_authority: V17_TERMINAL_REDUCER
- can_execute: false

## Expected Behavior

Automatic `workflow_run` Release admission on protected `main` must not publish a
failing check on the tip Render evaluates under `autoDeployTrigger=checksPass`
when an upstream Independent QA producer fails before uploading
`wow-v17-qa-policy-{run}-{attempt}`. Such cases must exit neutral with no
Release check and no PASS assertion. Strict/manual policy resolution remains
fail-closed.

## Actual Behavior

On main tip `e82af6a9cac0115952054e136f11d298195cc7cd`, `admit-release` failed
with `QA_POLICY_ARTIFACT_DUPLICATE_OR_MISSING` for upstream QA runs that never
uploaded a policy artifact (`TARGET_PR_IDENTITY_MISMATCH` on draft/retargeted
PR #2559). Live web/worker remained on ancestor
`e7384b9d2fed3256681b25715516dff2e97d3233`, so merged NHL #2553 / NFL #2552
could not roll out through `checksPass`.

## Root Cause

`resolve_qa_review_policy()` required exactly one matching QA policy artifact
before the draft/superseded neutralization path could run. A missing artifact
raised fail-closed even for the main-scoped opt-in (`allow_superseded_target`),
redding the unrelated protected-main tip.

Root cause status: CONFIRMED, confidence HIGH.

## Acceptance Criteria

1. Strict resolve (no opt-in) still raises `QA_POLICY_ARTIFACT_DUPLICATE_OR_MISSING`
   when the policy artifact is missing or duplicated.
2. With `allow_superseded_target=True`, a missing policy artifact returns
   `disposition=neutral` / `reason=RELEASE_QA_POLICY_ARTIFACT_ABSENT`.
3. Duplicate artifacts remain fail-closed even with the opt-in.
4. Existing superseded/draft neutrality and admission-diagnostic contracts still pass.
5. `can_execute=false` and V17 terminal authority are unchanged.

## Linked Engineering Fixes

- FIX-2026-11-10-001
