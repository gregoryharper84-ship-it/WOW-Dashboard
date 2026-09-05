# PM-2026-09-05-001 — Nightly incident ledger path drift

- status: DIAGNOSED
- severity: P1
- domain: CI / observability
- created_utc: 2026-09-05T07:56:00Z
- runtime_generation: V17_ACTIVE
- terminal_authority: V17_TERMINAL_REDUCER
- can_execute: false

## Impact

The scheduled `wow-v17-nightly-engineering-scan` deterministically fails before contract and production probes, so the repository's nightly detector cannot complete its required acceptance sequence.

## Evidence

GitHub Actions run `33872136915` failed in `Validate postmortem and engineering-fix ledger` with `ValueError: missing postmortem file for PM-2026-09-04-001`. The ledger stores the existing PM/FIX paths as `v17/...`, while the files are actually under `artifacts/wow-engine/v17/...`. Current `main` retains the same mismatched path metadata, so the failure remains reproducible.

## Reproduction

From repository `main`, execute from `artifacts/wow-engine`:

`python v17/nightly_incident_records.py validate`

The validator resolves the stale ledger path from repository root and cannot find the existing PM record.

## Root Cause

The first incident records were persisted with paths relative to `artifacts/wow-engine` (`v17/...`) while the current validator resolves ledger paths relative to repository root. The stored metadata and resolver contract therefore disagree.

## Governance Classification

R0 metadata/CI acceptance defect. No model math, probability calibration, terminal precedence, Action schema, auth, secrets, persistence database semantics, or execution authority are changed.

## Linked Engineering Fixes

- FIX-2026-09-05-001

## Closure Criteria

Correct legacy PM/FIX path metadata to repository-root-relative paths, add deterministic regression coverage that validates every checked-in incident path exists, pass protected CI, merge, and verify the next nightly validation no longer fails while `can_execute=false` remains intact.
