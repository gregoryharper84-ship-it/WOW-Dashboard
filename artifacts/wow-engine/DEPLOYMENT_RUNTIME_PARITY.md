# WOW Deployment / Runtime Parity Contract

Status: implementation acceptance requirement
Scope: governed production services, including the LLP Team Betting Engine / Full Model probability lane

## Why this exists

A healthy source contract is not sufficient evidence that production is running the intended implementation. A probability/model failure must not be inferred merely because a production request fails when the serving process is stopped, the deployed commit is stale, or the configured Render service has not been manually redeployed.

The observed failure mode that motivated this contract was:

- `/health` returned HTTP 200 repeatedly through approximately 17:41:49 UTC.
- The runtime then shut down cleanly at approximately 17:41:54 UTC (`Shutting down` -> `Application shutdown complete` -> `Finished server process`).
- The live Render deployment was commit `da33cbe4739b98bd0975d9a665dfb620ccca7190` (`v16: add NFL moneyline P1 immutable data backbone`).
- The intended Team Betting Engine integration patch was not identifiable as the production deploy.
- The service was configured with `autoDeploy = no`, so a GitHub push alone could not establish production parity.

Therefore this condition is classified as **deployment/runtime parity failure**, not as proof of model unavailability.

## Required diagnostic separation

Production acceptance and incident handling MUST distinguish these layers:

| Layer | Meaning |
| --- | --- |
| Source contract | Intended code/integration exists in repository source. |
| Deployment parity | Production deploy commit contains the intended source change. |
| Runtime liveness | Serving process remains alive and responds to health checks. |
| Model capability | Required fitted model, calibrator, evidence and probability path are available. |
| Connector/client path | External caller can reach the healthy service. |

A failure in deployment parity or runtime liveness MUST NOT automatically emit or justify `MODEL_UNAVAILABLE`.

`MODEL_UNAVAILABLE` remains reserved for an actual controlling-specialist/model capability failure or missing mandatory model inputs under the governing model contract.

## Production acceptance barrier

Before declaring the LLP Team Betting Engine / Full Model integration production-ready, the acceptance run MUST verify all of the following:

1. **Intended commit parity**
   - Capture the intended Git commit SHA.
   - Capture the live Render deploy commit SHA.
   - Require equality, or explicitly prove that the live SHA contains the intended patch.
   - If `autoDeploy = no`, require a deliberate manual deploy after the intended commit reaches the configured branch.

2. **Runtime liveness**
   - `/health` returns HTTP 200 after deployment.
   - Recheck after startup stabilization; a single successful health response is not enough.
   - Treat a clean process shutdown as `RUNTIME_STOPPED`, not model failure.

3. **Three smoke tests after parity is established**
   - probability-only winners request routes to the Full Model probability lane;
   - probability-only upset request routes to the Full Model probability lane and can rank only candidates with valid calibrated lower bounds;
   - bet/value/edge request routes to the market/edge LLP runner and preserves market-specific blockers without erasing completed sporting probability.

Acceptance MUST fail closed when any of the above is unverified.

## Suggested machine-readable status vocabulary

Use these diagnostic statuses where implementation surfaces a production-readiness result:

- `SOURCE_CONTRACT_PASS`
- `DEPLOYMENT_PARITY_UNVERIFIED`
- `DEPLOYMENT_PARITY_FAIL`
- `DEPLOYMENT_PARITY_PASS`
- `RUNTIME_HEALTHY`
- `RUNTIME_STOPPED`
- `MODEL_CAPABILITY_AVAILABLE`
- `MODEL_CAPABILITY_UNAVAILABLE`
- `CONNECTOR_PATH_FAIL`
- `PRODUCTION_ACCEPTANCE_PASS`
- `PRODUCTION_ACCEPTANCE_BLOCKED`

The reducer must preserve causality. In particular:

- `RUNTIME_STOPPED` -> `PRODUCTION_ACCEPTANCE_BLOCKED`
- `DEPLOYMENT_PARITY_UNVERIFIED|FAIL` -> `PRODUCTION_ACCEPTANCE_BLOCKED`
- neither condition alone -> `MODEL_CAPABILITY_UNAVAILABLE`

## Current operational implication

If the source integration is updated but Render is still serving an older commit, the next corrective action is deployment/runtime parity: place the intended commit on the configured production branch, deliberately redeploy when auto-deploy is disabled, verify stable health, and only then execute the smoke-test suite.

This contract does not weaken model governance, calibrated-probability requirements, lower-bound requirements, or `can_execute=false` restrictions. It only prevents infrastructure/deployment failures from being mislabeled as model failures.
