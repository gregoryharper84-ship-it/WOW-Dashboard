# FIX-2026-09-04-001 — Align Render checksPass with main CI

- status: FIX_IN_PROGRESS
- linked_postmortem: PM-2026-09-04-001
- risk: R1
- created_utc: 2026-09-04T07:49:42Z
- runtime_generation: V17_ACTIVE
- terminal_authority: V17_TERMINAL_REDUCER
- can_execute: false

## Root Cause Being Repaired

The production service now uses Render `checksPass`, but the controlling GitHub workflow does not run on pushes to `main`, and `render.yaml` still encodes `off`. This leaves the live setting unsupported by repository CI and vulnerable to Blueprint drift.

## Allowed Files

- `.github/workflows/wow-engine-verify.yml`
- `render.yaml`
- `artifacts/wow-engine/test_render_checks_pass_contract.py`
- `artifacts/wow-engine/v17/postmortems/PM-2026-09-04-001__render-checkspass-ci-trigger-drift.md`
- `artifacts/wow-engine/v17/engineering-fixes/FIX-2026-09-04-001__align-render-checkspass-with-main-ci.md`
- `artifacts/wow-engine/v17/incident-ledger.json`

Protected by exclusion: model/probability/calibration code, terminal reducer/labels, Action/OpenAPI contracts, auth/secrets, migrations/RLS, branch protection, and execution controls.

## Build Packet

```yaml
change_id: WOW-PATCH-2026-09-04-RENDER-CHECKSPASS-CI-CONTRACT
objective: Make Render checks-pass auto-deploy reproducible and repository-governed for the production V17 service.
current_problem: Current main commit has no workflow/check run while Render expects checksPass; render.yaml still declares off.
binding_authority: .agents/skills/wow-nightly-engineering-autopilot/SKILL.md + .agents/skills/wow-replit-patch-governor/SKILL.md
allowed_files:
  - .github/workflows/wow-engine-verify.yml
  - render.yaml
  - artifacts/wow-engine/test_render_checks_pass_contract.py
  - incident PM/FIX/ledger artifacts above
protected_files: all probability/model/calibration/terminal/action-schema/auth/secret/migration/RLS/branch-protection files not explicitly allowed
schema_changes: none
api_contract_changes: none
non_negotiable_invariants:
  - can_execute=false remains unconditional
  - WOW_DRY_RUN_ONLY remains true
  - V17_TERMINAL_REDUCER remains sole terminal authority
  - no branch-protection weakening
  - no secret changes
acceptance_tests:
  - wow-engine-verify runs on pull_request to main
  - wow-engine-verify runs on push to main
  - render.yaml production service uses autoDeployTrigger checksPass
  - workflow contract assertion expects checksPass
  - current execution/dry-run assertions remain unchanged
mandatory_regressions:
  - full wow-engine-verify PR workflow
  - python -m pytest -q via required workflow
fresh_production_verification:
  - merge through protected checks
  - observe a fresh post-merge main workflow run
  - observe Render auto-deploy of the merge commit without manual trigger
  - confirm runtime ACTIVE, terminal reducer authority, can_execute=false, /health 200, synthetic acceptance PASS
rollback_condition: any required check failure, missing post-merge push check, failed auto-deploy, or degraded production acceptance
publish_authorized: true
can_execute: false
```

## Implementation

Pending. Use the smallest complete change: add `push: branches: [main]`, change only the production web service Blueprint trigger from `off` to `checksPass`, update the existing CI assertion, and add a deterministic contract regression test.

## Regression Test

Pending. The new test must fail against the pre-repair repository because `push` is absent and `render.yaml` is `off`, then pass after the bounded repair.

## Validation Gates

- targeted reproduction
- relevant unit/integration tests
- WOW regressions
- can_execute=false and dry-run invariants
- diff-boundary verification
- required GitHub checks

## Deployment

Pending automatic deployment only after protected merge and post-merge CI checks pass. Do not manually trigger Render for this repair because production auto-deploy is enabled.

## Production Verification

Pending fresh production evidence.

## Rollback

Revert the single repair merge if the post-merge workflow/deploy chain or production verification fails.
