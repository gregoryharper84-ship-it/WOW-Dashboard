# FIX-2026-09-05-002 — Repair nightly OpenAPI description serialization

- status: IN_PROGRESS
- linked_postmortem: PM-2026-09-05-002
- risk: R1
- created_utc: 2026-09-05T09:59:00Z
- runtime_generation: V17_ACTIVE
- terminal_authority: V17_TERMINAL_REDUCER
- can_execute: false

## Root Cause Being Repaired

The canonical `/v17/daily-snapshot-run` 200 response used a YAML flow mapping whose unquoted prose description contained a comma. YAML parsed the text after the comma as a second response-object property, making the OpenAPI document invalid.

## Allowed Files

- `artifacts/wow-engine/v17/openapi.wow-betting-engine.v17.yaml`
- `artifacts/wow-engine/test_v17_openapi_description_serialization.py`
- `artifacts/wow-engine/v17/incident-ledger.json`
- `artifacts/wow-engine/v17/postmortems/PM-2026-09-05-002__nightly-canonical-openapi-description-serialization.md`
- `artifacts/wow-engine/v17/engineering-fixes/FIX-2026-09-05-002__repair-nightly-openapi-description-serialization.md`

## Protected Files

All probability/model/calibration implementations, terminal reducer/label code, request/response field semantics, database migrations/RLS, auth/secrets, branch protection, Render environment values, and live-execution controls.

## API / Schema / Runtime Effects

No semantic API change. The intended description string remains `Terminal bounded Daily receipt, including held rows`; the repair only ensures YAML serializes it as one `description` value rather than an invalid second property.

## Implementation

Quote the affected Daily 200 response description and add a regression that validates the full canonical V17 OpenAPI document and asserts the parsed response object contains only `description` with the intended value.

## Validation Gates

- deterministic OpenAPI validator reproduction removed
- targeted serialization regression
- full governed probability backend suite
- required-three regression
- additional required regression
- main-triggered nightly scan reaches production probe
- `can_execute=false` remains invariant

## Deployment

No manual deployment is authorized or required. Render remains configured for `checksPass`; any resulting deployment must follow protected CI automatically.

## Rollback

If this bounded serialization repair is later identified as causal, deterministic rollback is a git revert of the merge commit. No model/persistence migration is involved.
