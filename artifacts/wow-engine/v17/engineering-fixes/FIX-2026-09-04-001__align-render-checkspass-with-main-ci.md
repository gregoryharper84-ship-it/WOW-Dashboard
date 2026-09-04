# FIX-2026-09-04-001 — Align Render checksPass with main CI

- status: VERIFIED_CLOSED
- linked_postmortem: PM-2026-09-04-001
- risk: R1
- created_utc: 2026-09-04T07:49:42Z
- verified_utc: 2026-09-04T08:01:56Z
- runtime_generation: V17_ACTIVE
- terminal_authority: V17_TERMINAL_REDUCER
- can_execute: false

## Root Cause Being Repaired

The production service used Render `checksPass`, but the controlling GitHub workflow did not run on pushes to `main`, and `render.yaml` encoded `off`. This left the live setting unsupported by repository CI and vulnerable to Blueprint drift.

## Allowed Files

- `.github/workflows/wow-engine-verify.yml`
- `render.yaml`
- `artifacts/wow-engine/test_render_checks_pass_contract.py`
- linked incident PM/FIX/ledger artifacts

Protected by exclusion: model/probability/calibration code, terminal reducer/labels, Action/OpenAPI contracts, auth/secrets, migrations/RLS, branch protection, and execution controls.

## Build Packet

`WOW-PATCH-2026-09-04-RENDER-CHECKSPASS-CI-CONTRACT`, R1, publish authorized under the nightly bounded-repair policy. No schema or API contract changes. Required invariants remained `can_execute=false`, `WOW_DRY_RUN_ONLY=true`, and `V17_TERMINAL_REDUCER` sole terminal authority.

## Implementation

- Added `push: branches: [main]` to `.github/workflows/wow-engine-verify.yml` while preserving pull-request and manual triggers.
- Changed only the production `wow-governed-probability-engine` Blueprint trigger from `off` to `checksPass`; the worker remained `off`.
- Updated the existing release-contract assertion to require `checksPass`.
- Added `test_render_checks_pass_contract.py` to lock the post-merge CI trigger, Blueprint trigger, `WOW_CAN_EXECUTE=false`, and `WOW_DRY_RUN_ONLY=true` invariants.

## Regression Test

PR #187 head `716149c639b58e99d0d30bed913f611a98b7c5f4` passed all three observed protected checks:

- WOW governed probability backend — success
- WOW required-three regression — success
- WOW additional required regression — success

The governed backend workflow included the full backend pytest suite, durable Postgres/Redis/PostgREST/Celery integration, and Render/event-schema/Custom-GPT Action contract validation.

## Validation Gates

Passed. Diff was limited to the declared CI/deployment/test/incident-record files. No protected probability/model/calibration/terminal/Action/auth/secret/schema/RLS/branch-protection files were modified.

## Deployment

PR #187 merged through protected checks as commit `12e2b823c3115e05c3631fe0c611b2d47fb4f40e`. That exact commit generated a new `wow-engine-verify` `push` run on `main`, which completed successfully. Render then automatically created deploy `dep-dad7knqjnfac73e9jfv0` with trigger `new_commit`; no manual/API deployment was invoked. It reached `live` at 2026-09-04T08:00:52Z.

## Production Verification

Fresh Render evidence after the automatic deploy:

- `WOW_V17_RUNTIME status=ACTIVE`
- `global_terminal_authority=V17_TERMINAL_REDUCER`
- `can_execute=false`
- repeated `GET /health` → 200
- `POST /score-pick-request` → 200
- `POST /score-team-event-request` → 200
- `WOW_MLB_1IP_FINAL_REFRESH status=PASS`
- `WOW_V17_SYNTHETIC_SELF_ACCEPTANCE status=PASS`, one attempt for prop/team-event/projected-lineup lanes
- no new production P0/P1 error was observed in the verification window

## Rollback

No rollback required. If this deployment contract regresses, revert merge commit `12e2b823c3115e05c3631fe0c611b2d47fb4f40e`; do not weaken branch protection or safety invariants.
