# FIX-2026-11-10-001 — Neutralize absent QA policy on main-scoped admission

- status: IMPLEMENTATION_COMPLETE_PENDING_REVIEW
- risk: R1
- primary_subsystem: DEPLOYMENT_RUNTIME
- linked_postmortem: PM-2026-11-10-001
- created_utc: 2026-11-10T15:45:00Z
- can_execute: false

## Objective

Stop automatic main-scoped `admit-release` from failing protected-main tips when
an upstream QA producer never uploaded a policy artifact, unblocking Render
`checksPass` for already-merged main commits without weakening strict Release
policy resolution.

## Change summary

In `.github/scripts/independent_source_review_receipt.py` `resolve_qa_review_policy`:

- when inventory matches for `wow-v17-qa-policy-{run}-{attempt}` are empty **and**
  `allow_superseded_target` is true, return
  `{disposition: neutral, reason: RELEASE_QA_POLICY_ARTIFACT_ABSENT}`;
- otherwise keep `QA_POLICY_ARTIFACT_DUPLICATE_OR_MISSING` fail-closed for
  missing (strict path) and duplicate (all paths).

Register `RELEASE_QA_POLICY_ARTIFACT_ABSENT` in `failure_codes.md`.

## Tests

- `test_main_scoped_admission_neutralizes_absent_qa_policy_artifact`
- existing missing/duplicate/expired strict rejects
- existing superseded neutrality
- `test_v17_release_admission_diagnostics`

## Rollback

Revert the merge commit for this fix. Strict missing-policy raise is restored.

## Non-goals

- No probability, calibration, Action auth, or `can_execute` changes.
- Does not certify or publish sporting models.
- Does not manually trigger Render deploy; `checksPass` remains authoritative.
