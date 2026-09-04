# PM-2026-09-04-001 — Render checksPass CI trigger drift

- status: VERIFIED_CLOSED
- severity: P1
- domain: deployment / CI
- created_utc: 2026-09-04T07:49:42Z
- closed_utc: 2026-09-04T08:01:56Z
- runtime_generation: V17_ACTIVE
- terminal_authority: V17_TERMINAL_REDUCER
- can_execute: false

## Impact

The production Render service was configured for `autoDeploy=yes` with `autoDeployTrigger=checksPass`, but the repository's controlling workflow only ran on pull requests and manual dispatch. A new merge commit on `main` therefore received no GitHub Actions run/check, so Render's checks-pass trigger had no post-merge CI signal to consume. In addition, `render.yaml` still declared the production service `autoDeployTrigger: "off"`, so a future Blueprint sync could revert the live dashboard setting.

## Evidence

- Pre-repair GitHub `main` commit `b522eb8a72527b4d18dc24ddfa362b68b40db754` had zero check runs and zero workflow runs.
- Pre-repair `.github/workflows/wow-engine-verify.yml` declared only `pull_request` and `workflow_dispatch` triggers.
- Render production reported `autoDeploy=yes`, `autoDeployTrigger=checksPass`, branch `main`.
- Pre-repair live production deploy `dep-dad4i2qjnfac73edie8g` was triggered by API, not checks-pass auto-deploy.
- Pre-repair `render.yaml` declared `autoDeployTrigger: "off"` for `wow-governed-probability-engine`.

## Reproduction

1. Inspect the pre-repair workflow: no `push` trigger existed for `main`.
2. Query GitHub Actions/check-runs for pre-repair main `b522eb8a72527b4d18dc24ddfa362b68b40db754`: both were empty.
3. Query Render service configuration: `autoDeployTrigger=checksPass`.
4. Query the pre-repair `render.yaml`: production service declared `off`.

This was deterministic configuration/CI drift.

## Root Cause

The live Render Auto-Deploy setting was changed to `After CI Checks Pass` without synchronizing the repository Blueprint contract or adding a post-merge `push` CI trigger. PR checks protected merges, but Render's checks-pass deployment mode required a check signal on the branch commit actually being deployed.

## Governance Classification

Risk R1: bounded CI/deployment configuration repair only. No probability math, calibration, terminal semantics, Action schema, auth, secrets, database schema, RLS, branch protection, or execution capability changes.

## Linked Engineering Fixes

- FIX-2026-09-04-001

## Closure Evidence

- PR #187 passed `WOW governed probability backend`, `WOW required-three regression`, and `WOW additional required regression` before merge.
- Protected merge commit: `12e2b823c3115e05c3631fe0c611b2d47fb4f40e`.
- A fresh `wow-engine-verify` run was created by the `push` event on that exact main commit and completed successfully.
- Render automatically created deploy `dep-dad7knqjnfac73e9jfv0` for that commit with trigger `new_commit`; no manual/API deploy was used.
- Deploy reached `live` at 2026-09-04T08:00:52Z.
- Fresh production logs showed `WOW_V17_RUNTIME status=ACTIVE global_terminal_authority=V17_TERMINAL_REDUCER can_execute=false`, repeated `/health` 200 responses, `/score-pick-request` 200, `/score-team-event-request` 200, MLB 1IP refresh PASS, and `WOW_V17_SYNTHETIC_SELF_ACCEPTANCE status=PASS`.

## Closure Criteria

Satisfied. The post-merge CI signal now exists, repository Blueprint and CI contract match Render `checksPass`, automatic deployment was demonstrated on the merge commit, and fresh production evidence preserved all V17 safety invariants.
