# PM-2026-09-05-001 — Nightly incident ledger path drift

- status: VERIFIED_CLOSED
- severity: P1
- domain: CI / observability
- created_utc: 2026-09-05T07:56:00Z
- closed_utc: 2026-09-05T08:06:25Z
- runtime_generation: V17_ACTIVE
- terminal_authority: V17_TERMINAL_REDUCER
- can_execute: false

## Impact

The scheduled `wow-v17-nightly-engineering-scan` deterministically failed before contract and production probes, so the repository's nightly detector could not complete its required acceptance sequence.

## Evidence

GitHub Actions run `33872136915` failed in `Validate postmortem and engineering-fix ledger` with `ValueError: missing postmortem file for PM-2026-09-04-001`. The ledger stored the existing PM/FIX paths as `v17/...`, while the files are actually under `artifacts/wow-engine/v17/...`.

## Reproduction

From the pre-repair repository state, executing `python v17/nightly_incident_records.py validate` from `artifacts/wow-engine` deterministically failed because the validator resolved the stale ledger path from repository root.

## Root Cause

The first incident records were persisted with paths relative to `artifacts/wow-engine` (`v17/...`) while the current validator resolves ledger paths relative to repository root. The stored metadata and resolver contract therefore disagreed.

## Governance Classification

R0 metadata/CI acceptance defect. No model math, probability calibration, terminal precedence, Action schema, auth, secrets, persistence database semantics, or execution authority changed.

## Linked Engineering Fixes

- FIX-2026-09-05-001

## Closure Evidence

PR #214 merged as `f61b37d87e4a3e8f317de2d0d103ff4c17433e4d` after all protected PR checks passed. The post-merge `wow-engine-verify` push run also passed, including the governed backend suite, durable runtime integration, and Render/event/Custom-GPT contract validation. Render auto-deployed the exact merge commit as `dep-dadsq7h5efls739ekm2g`, which reached LIVE. Fresh startup evidence reported `WOW_V17_RUNTIME status=ACTIVE global_terminal_authority=V17_TERMINAL_REDUCER can_execute=false`; `/health`, `/score-pick-request`, `/score-team-event-request`, and `/score-team-event` returned 200; MLB 1IP final refresh passed; synthetic V17 self-acceptance passed in one attempt per canonical lane. No new error-level Render logs were observed.

## Closure Criteria

Satisfied. Repository-root-relative incident paths are now validated by a deterministic regression, protected CI is green, the exact merge commit is live, and production safety invariants remain intact.
