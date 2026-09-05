# FIX-2026-09-05-001 — Repair nightly ledger path metadata

- status: FIX_IN_PROGRESS
- linked_postmortem: PM-2026-09-05-001
- risk: R0
- created_utc: 2026-09-05T07:57:00Z
- runtime_generation: V17_ACTIVE
- terminal_authority: V17_TERMINAL_REDUCER
- can_execute: false

## Root Cause Being Repaired

The initial incident ledger entries store `postmortem_path` and engineering-fix `path` relative to `artifacts/wow-engine`, but `nightly_incident_records.py` resolves stored paths relative to repository root. The mismatch makes ledger validation fail deterministically.

## Allowed Files

- `artifacts/wow-engine/v17/incident-ledger.json`
- `artifacts/wow-engine/v17/test_nightly_incident_records.py`
- `artifacts/wow-engine/v17/postmortems/PM-2026-09-05-001__nightly-incident-ledger-path-drift.md`
- `artifacts/wow-engine/v17/engineering-fixes/FIX-2026-09-05-001__repair-nightly-ledger-path-metadata.md`

## Protected Files

All probability/model/calibration code, terminal reducer/label code, Action/OpenAPI contracts, database migrations/RLS, auth/secrets, branch protection, Render environment values, and live-execution controls.

## API / Schema / Runtime Effects

None. No route, API contract, database schema, model output, terminal label, governance precedence, or runtime behavior changes.

## Implementation

Correct the stale legacy ledger paths to repository-root-relative paths and add this PM/FIX pair using the same canonical path convention. Add a deterministic test that calls the ledger validator against the checked-in repository state.

## Regression Test

`artifacts/wow-engine/v17/test_nightly_incident_records.py` must prove `validate()` succeeds and the incident ledger retains `V17_ACTIVE`, `V17_TERMINAL_REDUCER`, and `can_execute=false`.

## Validation Gates

- original deterministic validator failure is removed
- targeted incident-record test passes
- full governed probability backend regression suite passes through protected CI
- nightly ledger validation passes
- required Action/V17 contract validation remains green
- can_execute=false and dry-run invariants remain unchanged
- diff contains only allowed files
- protected GitHub checks are green

## Deployment

No runtime deployment is required for the functional repair. Because the repository is Render auto-deployed after checks pass, a merge may produce a no-op application redeploy; production must still be checked for health and unchanged safety invariants.

## Production Verification

Pending fresh post-merge evidence.

## Rollback

Revert the single patch merge if protected CI or production verification exposes a regression.
