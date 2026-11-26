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

The authenticated `GET /wow/deploy-identity` readback (below) is the mechanism that supplies `DEPLOYMENT_PARITY_PASS|FAIL|UNVERIFIED` without a Render API key.

## Authenticated deploy-identity readback (`GET /wow/deploy-identity`)

`wow-agent-worker` is a Render Python worker with no HTTP surface, so its deployed commit used to be readable only through the Render API or dashboard. That dependency is now removed for the common case:

- On Celery `worker_ready`, the worker pins its own `RENDER_GIT_COMMIT` into every enabled `wow_agent_worker_registry` row (`agent_runtime/worker_release.py`). One atomic `UPDATE` covers all rows, so the pinned set is always uniform (last writer wins).
- The governed web service compares that last observed pin with its own `RENDER_GIT_COMMIT` and exposes the verdict through `GET /wow/deploy-identity` (`agent_runtime_api.py`).

The route is read-only. It carries no probability, grading or execution authority, and always reports `can_execute: false`.

### Authentication

The route requires the existing governed action credential, sent as `Authorization: Bearer <WOW_ACTION_API_KEY>`. A missing or wrong credential returns HTTP 401. The route is not public; deploy identity is not advertised to unauthenticated callers.

### Parity contract

| HTTP status | `parity` | Meaning |
| --- | --- | --- |
| 200 | `MATCH` | The single commit pinned by the worker equals this web process's commit. |
| 409 | `MISMATCH` | Both SHAs are known and differ: real web/worker deploy drift. |
| 503 | `UNVERIFIED` | Parity was not established. Never a default `MATCH`. |

`UNVERIFIED` is returned with a typed `parity_reason`, never free-form upstream text:

- `WORKER_RUNTIME_COMMIT_MISSING` — no worker commit is pinned, or the pinned set is not exactly one valid SHA. The only realistic cause is that no redeployed worker has stamped the migration-enabled columns yet.
- `WEB_RUNTIME_COMMIT_UNKNOWN` — this web process has no valid `RENDER_GIT_COMMIT`, so there is nothing to compare against.
- `WORKER_RUNTIME_COMMIT_UNREADABLE` — the registry read itself failed. No SHA is inferred from a failed read.

Response body (the same object is used as the `detail` of the 409/503 responses):

```json
{
  "web_deployed_sha": "<40-hex or null>",
  "worker_deployed_sha": "<40-hex, only when exactly one SHA is pinned, else null>",
  "worker_registry": {
    "rows_missing_runtime_commit": 0,
    "recorded_at": "<ISO-8601 worker pin timestamp or null>"
  },
  "parity": "MATCH | MISMATCH | UNVERIFIED",
  "parity_reason": null,
  "can_execute": false
}
```

### Fail-closed recording

A missing or malformed `RENDER_GIT_COMMIT` writes nothing: the worker never fabricates a SHA and never blanks a previously pinned value. The readback therefore reports `UNVERIFIED` rather than a guess. A registry write failure does not stop governed worker processing, but it is logged with a typed code (`WOW_WORKER_RUNTIME_COMMIT_UNVERIFIED` / `WOW_WORKER_RUNTIME_COMMIT_RECORD_FAILED`) and the readback stays `UNVERIFIED`.

### What this proves, and what it does not

- It proves **environment parity**: both Render services self-report the same commit. It does not independently prove that the loaded code equals that commit.
- The pin is a **start-time stamp**, not a liveness heartbeat. It reads "the most recent worker start was at commit X", not "a worker is live on X now". Runtime liveness remains a separate layer (`/health`, `/health/ready`) under the diagnostic separation above.
- A `MISMATCH` or `UNVERIFIED` is deployment/runtime evidence only. It must not be reported as `MODEL_UNAVAILABLE`, and it blocks production acceptance under the existing reducer.

### Prerequisites before it stops returning `UNVERIFIED`

Applying this code to `main` is not sufficient. Two operational steps are required, in order:

1. **Apply migration `migrations/20261126_worker_registry_runtime_commit.sql`** to the governed Supabase database. It adds the nullable `runtime_commit` and `runtime_commit_recorded_at` columns to `wow_agent_worker_registry` plus a 40-hex check constraint; it is idempotent and does not overwrite existing rows.
2. **Redeploy `wow-agent-worker`.** The pin is written only on worker startup, by code that a running worker does not have. A worker deployed before this change never stamps the columns, so the readback keeps returning `503 UNVERIFIED` with `WORKER_RUNTIME_COMMIT_MISSING` until it is redeployed.

Until both steps are done, and the web service is serving this route's code, `503 UNVERIFIED` is the expected and correct response. It is not evidence of drift, a broken worker, or model failure, and it must be reported as unverified rather than as parity.

## Current operational implication

If the source integration is updated but Render is still serving an older commit, the next corrective action is deployment/runtime parity: place the intended commit on the configured production branch, deliberately redeploy when auto-deploy is disabled, verify stable health, and only then execute the smoke-test suite.

This contract does not weaken model governance, calibrated-probability requirements, lower-bound requirements, or `can_execute=false` restrictions. It only prevents infrastructure/deployment failures from being mislabeled as model failures.
