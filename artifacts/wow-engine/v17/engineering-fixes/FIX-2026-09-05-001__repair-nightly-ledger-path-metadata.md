# FIX-2026-09-05-001 — Repair nightly ledger path metadata

- status: VERIFIED_CLOSED
- linked_postmortem: PM-2026-09-05-001
- risk: R0
- created_utc: 2026-09-05T07:57:00Z
- closed_utc: 2026-09-05T08:06:25Z
- runtime_generation: V17_ACTIVE
- terminal_authority: V17_TERMINAL_REDUCER
- can_execute: false

## Root Cause Being Repaired

The initial incident ledger entries stored `postmortem_path` and engineering-fix `path` relative to `artifacts/wow-engine`, but `nightly_incident_records.py` resolves stored paths relative to repository root. The mismatch made ledger validation fail deterministically.

## Allowed Files

- `artifacts/wow-engine/v17/incident-ledger.json`
- `artifacts/wow-engine/v17/test_nightly_incident_records.py`
- `artifacts/wow-engine/v17/postmortems/PM-2026-09-05-001__nightly-incident-ledger-path-drift.md`
- `artifacts/wow-engine/v17/engineering-fixes/FIX-2026-09-05-001__repair-nightly-ledger-path-metadata.md`

## Protected Files

All probability/model/calibration code, terminal reducer/label code, Action/OpenAPI contracts, database migrations/RLS, auth/secrets, branch protection, Render environment values, and live-execution controls.

## API / Schema / Runtime Effects

None. No route, API contract, database schema, model output, terminal label, governance precedence, or runtime behavior changed.

## Implementation

Corrected the stale legacy ledger paths to repository-root-relative paths and added the linked PM/FIX pair using the same canonical path convention. Added a deterministic test that calls the ledger validator against the checked-in repository state and verifies every PM/FIX path exists.

## Regression Test

`artifacts/wow-engine/v17/test_nightly_incident_records.py` validates the checked-in ledger and locks `V17_ACTIVE`, `V17_TERMINAL_REDUCER`, and `can_execute=false`.

## Validation Gates

- original deterministic validator failure removed: PASS
- targeted incident-record regression: PASS through governed backend CI
- full governed probability backend regression suite: PASS
- durable Agent Runtime integration: PASS
- Render/event schema/Custom GPT Action contract validation: PASS
- required-three regression: PASS
- additional required regression: PASS
- can_execute=false and governance invariants unchanged: PASS
- diff boundary limited to four allowed files: PASS
- protected GitHub checks: PASS

## Deployment

PR #214 merged as `f61b37d87e4a3e8f317de2d0d103ff4c17433e4d`. Render checks-pass auto-deploy created `dep-dadsq7h5efls739ekm2g` for that exact commit and the deployment reached LIVE.

## Production Verification

Fresh Render evidence after deployment reported `WOW_V17_RUNTIME status=ACTIVE`, `global_terminal_authority=V17_TERMINAL_REDUCER`, and `can_execute=false`. `/health` returned 200. Canonical `/score-pick-request`, `/score-team-event-request`, and `/score-team-event` acceptance requests returned 200. `WOW_MLB_1IP_FINAL_REFRESH status=PASS`. `WOW_V17_SYNTHETIC_SELF_ACCEPTANCE status=PASS` with one attempt for prop, team-event, and projected-lineup paths. No new error-level production logs were observed.

## Rollback

Deterministic rollback remains `git revert f61b37d87e4a3e8f317de2d0d103ff4c17433e4d` if a later regression is attributed to this bounded metadata/test repair. No rollback was required.
