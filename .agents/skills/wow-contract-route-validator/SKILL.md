---
name: wow-contract-route-validator
description: Prevent phantom production endpoints and stale acceptance schemas from reaching production verification.
---

# WOW Contract / Route Validator

## Mission
Maintain the machine-readable allowlist of production acceptance surfaces and fail CI on route, operation, request-schema, response-schema, or required-invariant drift.

## Source of truth
- Canonical production entrypoint: `api_ncaaf_acceptance:app`.
- Canonical Action contracts: `v17/openapi.wow-betting-engine.v17.yaml` and `v17/openapi.llp-team-engine.v17.yaml`.
- Sentinel allowlist: `v17/production_acceptance_routes.json`.
- Generator: `v17/generate_acceptance_routes.py`.

## Rules
- Runtime route discovery reads mounted FastAPI routes directly.
- Do not expose internal acceptance routes in public Action schemas merely to make validation easier.
- Request/response model schemas are deterministically hashed where resolvable.
- Generic response models are supplemented by an acceptance-contract hash over required headers and response invariants.
- Any allowlisted route missing from the production app is a CI failure.
- Any acceptance request targeting a non-allowlisted route is rejected before network access.
- `can_execute=false` and `V17_TERMINAL_REDUCER` remain invariant.
