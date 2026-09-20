# PM-2026-09-05-002 — Nightly canonical OpenAPI description serialization

- status: VERIFIED_CLOSED
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

## Closure Evidence (2026-09-20)

The bounded repair in FIX-2026-09-05-002 was already present on `main` at merge commit
`a8cc314476eddc59f4571b4943efc520e51dc254`, but this ledger record was left in
`FIX_IN_PROGRESS` and was never advanced to closure. Re-verification on 2026-09-20
confirms all closure criteria are independently satisfied by current evidence:

1. `python -m pytest artifacts/wow-engine/test_v17_openapi_description_serialization.py`
   passes locally against current `main` (`0d1c1a89ef3b58256351dab1bfccc2e4f10827ea`):
   the canonical document validates under `openapi-spec-validator` and the parsed
   `/v17/daily-snapshot-run` 200 response contains exactly one `description` field with
   the intended text.
2. The regression above enforces (2) directly; it was added by the same repair commit.
3. GitHub Actions run `35511352472` (`wow-v17-nightly-engineering-scan`, triggered on
   main commit `25747d16...`, a descendant of the repair commit) and run `35512943348`
   (`wow-verify` on current main tip `0d1c1a89...`) both concluded `success`.
4. Run `35511352472`'s `Validate V17 and Action contracts` step printed
   `validated v17/openapi.wow-betting-engine.v17.yaml` with no validator error, its
   `Run governed probability backend regression suite` step reported
   `2613 passed, 3 skipped`, and its `Probe production health and governance` step
   received `{"status":"ok", ...}` from the live Render service.
5. This repair never touched terminal-reducer or execution-safety code; both invariants
   remain byte-for-byte unchanged.

No new code change was required to close this incident — only the stale ledger/postmortem
status metadata needed correction to reflect the repair that already landed on `main`.
