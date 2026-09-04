# PM-2026-09-04-001 — Render checksPass CI trigger drift

- status: DIAGNOSED
- severity: P1
- domain: deployment / CI
- created_utc: 2026-09-04T07:49:42Z
- runtime_generation: V17_ACTIVE
- terminal_authority: V17_TERMINAL_REDUCER
- can_execute: false

## Impact

The production Render service is configured for `autoDeploy=yes` with `autoDeployTrigger=checksPass`, but the repository's controlling workflow only runs on pull requests and manual dispatch. A new merge commit on `main` therefore receives no GitHub Actions run/check, so Render's checks-pass trigger has no post-merge CI signal to consume. In addition, `render.yaml` still declares the production service `autoDeployTrigger: "off"`, so a future Blueprint sync could revert the live dashboard setting.

## Evidence

- GitHub `main` commit `b522eb8a72527b4d18dc24ddfa362b68b40db754` has zero check runs and zero workflow runs.
- `.github/workflows/wow-engine-verify.yml` declares only `pull_request` and `workflow_dispatch` triggers.
- Render production reports `autoDeploy=yes`, `autoDeployTrigger=checksPass`, branch `main`.
- The live production deploy for the current main commit is `dep-dad4i2qjnfac73edie8g` and was triggered by API, not by checks-pass auto-deploy.
- `render.yaml` declares `autoDeployTrigger: "off"` for `wow-governed-probability-engine`, and the CI contract currently asserts that stale value.

## Reproduction

1. Inspect `.github/workflows/wow-engine-verify.yml`: no `push` trigger exists for `main`.
2. Query GitHub Actions/check-runs for current main commit `b522eb8a72527b4d18dc24ddfa362b68b40db754`: both are empty.
3. Query Render service configuration: `autoDeployTrigger=checksPass`.
4. Query `render.yaml`: production service still declares `off`.

This is deterministic configuration/CI drift.

## Root Cause

The live Render Auto-Deploy setting was changed to `After CI Checks Pass` without synchronizing the repository Blueprint contract or adding a post-merge `push` CI trigger. PR checks protect merges, but Render's checks-pass deployment mode requires a check signal on the branch commit that is actually being deployed.

## Governance Classification

Risk R1: bounded CI/deployment configuration repair only. No probability math, calibration, terminal semantics, Action schema, auth, secrets, database schema, RLS, branch protection, or execution capability changes.

## Linked Engineering Fixes

- FIX-2026-09-04-001

## Closure Criteria

The repair must add a `push` trigger for `main`, align `render.yaml` and its CI assertion with `checksPass`, preserve `can_execute=false` and dry-run invariants, pass the protected PR checks, merge without bypass, auto-deploy from the post-merge checks-pass signal, and verify the resulting production deploy/runtime with fresh evidence.
