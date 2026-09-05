# PM-2026-09-05-002 — Nightly canonical OpenAPI description serialization

- status: REPAIR_IN_PROGRESS
- severity: P1
- domain: CI / Action contract validation
- created_utc: 2026-09-05T09:58:00Z
- runtime_generation: V17_ACTIVE
- terminal_authority: V17_TERMINAL_REDUCER
- can_execute: false

## Impact

The newly self-triggered `wow-v17-nightly-engineering-scan` completed the full governed backend test suite and incident-ledger validation, then failed before production probing because the canonical V17 OpenAPI document was not valid OpenAPI 3.1 under `openapi-spec-validator`.

## Evidence

GitHub Actions run `33959303327` on main commit `7cd8881a5ef173499a886d6405bedfd4aa20474b` reported `1133 passed, 3 skipped`, then failed in `Validate V17 and Action contracts` with `OpenAPIValidationError: Unevaluated properties are not allowed ('including held rows' was unexpected)`.

The failing YAML was the `/v17/daily-snapshot-run` 200 response:

```yaml
'200': {description: Terminal bounded Daily receipt, including held rows}
```

In YAML flow-mapping syntax, the unquoted comma split the intended description into a second mapping entry named `including held rows` with a null value.

## Reproduction

Load `artifacts/wow-engine/v17/openapi.wow-betting-engine.v17.yaml` with `yaml.safe_load` and run `openapi_spec_validator.validate_spec`. The parsed 200 response contains an unexpected `including held rows` property and validation fails deterministically.

## Root Cause

A prose comma was used inside an unquoted scalar embedded in a YAML flow mapping. The human-readable response description was semantically intended as one string, but YAML parsed it as two mapping entries.

## Governance Classification

R1 deterministic contract-serialization defect. The repair changes only YAML quoting/serialization so the already-intended description is represented correctly. It does not change route semantics, request/response fields, model probability, calibration, terminal precedence, auth, secrets, persistence behavior, or execution authority.

## Linked Engineering Fixes

- FIX-2026-09-05-002

## Closure Criteria

1. Canonical V17 OpenAPI validates under `openapi-spec-validator`.
2. Regression locks the Daily 200 response to exactly one description field with the intended text.
3. All protected GitHub checks pass.
4. A fresh main-triggered nightly scan passes contract validation and reaches production health/governance probing.
5. `V17_TERMINAL_REDUCER` and `can_execute=false` remain unchanged.
