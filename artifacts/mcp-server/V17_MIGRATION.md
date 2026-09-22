# WOW V17 Actions -> MCP/Plugin Migration

Status: `IMPLEMENTATION / LIVE_ACCEPTANCE_PARTIAL`

Change class: **Class B** probability-adjacent integration infrastructure. This package does not alter sporting-model mathematics, fitted artifacts, coefficients, calibration, lower bounds, qualification thresholds, model ownership, or terminal reduction.

## Non-negotiable invariants

- `custom_gpt_identity=WOW_BETTING_ENGINE`
- `runtime_generation=V17_ACTIVE`
- `terminal_authority=V17_TERMINAL_REDUCER`
- `can_execute=false`
- `DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true`
- exactly one controlling fitted specialist owns each sporting probability row
- sportsbook implied probability, external projections, recent results, narrative judgment, and generic LLM reasoning never substitute for governed fitted-model probability
- Action/MCP transport failures, scorer failures, malformed model output, missing inputs, and market-data failures preserve their distinct typed semantics and are never collapsed into `MODEL_UNAVAILABLE`

## Architecture

```text
ChatGPT / WOW Plugin
        |
        | OAuth 2.1 + Authorization Code/PKCE
        | standard identity scopes: openid email
        v
WOW V17 MCP gateway
        |
        | signed-token validation:
        | issuer + audience + exp/nbf + subject + client_id
        | V17 authorization: wow_mcp_permissions JWT claim
        |
        | Authorization: Bearer $WOW_ACTION_API_KEY
        | backend credential remains server-side only
        v
wow-governed-probability-engine.onrender.com
        |
        +-- V17_TERMINAL_REDUCER
        +-- governed fitted specialists
        +-- LLP team/event engine
        +-- immutable prediction receipts
        +-- recommendation ledger
        +-- Daily orchestration
```

The MCP layer is intentionally a thin adapter. It does not calculate sporting probabilities, calibrate outputs, own terminal decisions, or reconstruct Daily in the conversational layer.

The pre-existing `src/index.js` server remains a legacy WOW Data Hub compatibility surface. It is **not** the production V17 plugin replacement. The V17 migration uses the dedicated files prefixed `v17_` so generic/legacy tools cannot accidentally become probability authority.

## Governed tool surface

The migration preserves the existing Action operation IDs:

| MCP tool | Backend route | Required V17 permission | State |
| --- | --- | --- | --- |
| `getWowV17BackendHealth` | `GET /health` | `wow.runtime.read` | read-only |
| `getWowV17Governance` | `GET /governance` | `wow.governance.read` | read-only |
| `getWowV17HostContract` | `GET /v17/host-contract` | `wow.governance.read` | read-only |
| `getWowV17DetailedEvidenceContract` | `GET /v17/detailed-evidence-contract` | `wow.evidence.read` | read-only |
| `getWowV17Capabilities` | `GET /v17/capabilities` | `wow.governance.read` | read-only |
| `getWowV17RundownMarketHealth` | `GET /v17/market-health/rundown` | `wow.evidence.read` | read-only |
| `getWowV17OddsApiMarketHealth` | `GET /v17/market-health/odds-api` | `wow.evidence.read` | read-only |
| `getWowV17CompactEspnDiscovery` | `GET /v17/discovery/espn-compact` | `wow.evidence.read` | read-only |
| `scoreWowPickRequest` | `POST /score-pick-request` | `wow.predictions.score` | stateful |
| `scoreWowProp` | `POST /score-prop` | `wow.predictions.score` | stateful |
| `runWowV17DailySnapshot` | `POST /v17/daily-snapshot-run` | `wow.daily.run` | stateful |
| `readWowV17DailySnapshotRowDetail` | `GET /v17/daily-snapshot-run/{run_id}/rows` | `wow.predictions.read` | read-only |
| `scoreWowV17TeamEventFromWowHost` | `POST /score-team-event` | `wow.predictions.score` | stateful |
| `lookupWowV17PredictionReceipts` | `POST /v17/prediction-receipts/lookup` | `wow.predictions.read` | read-only |
| `recordWowV17Recommendations` | `POST /record-recommendations` | `wow.recommendations.write` | stateful |
| `settleWowV17Recommendations` | `POST /settle-recommendations` | `wow.settlements.write` | stateful |

There is deliberately no tool for placing, approving, modifying, routing, canceling, or executing a wager or market order.

## Exact-once / receipt recovery

For board scoring, the caller must keep `request_id` stable for the board and `row_key` stable and unique per directional row.

On a timeout, disconnect, or otherwise ambiguous `scoreWowPickRequest` completion:

1. do **not** reinterpret the transport condition as `MODEL_UNAVAILABLE`;
2. call `lookupWowV17PredictionReceipts` using the same stable row identity;
3. if the immutable receipt exists, resume from the durable result;
4. only if the receipt is proven absent may the unresolved row be retried with the same `request_id` and `row_key`.

The MCP gateway returns `ACTION_TRANSPORT_TIMEOUT` / `ACTION_TRANSPORT_FAILURE` and names `lookupWowV17PredictionReceipts` as the recovery operation for ambiguous Pick Request transport failure. It does not auto-retry stateful scoring.

## Runtime modes

### Local / protocol acceptance

```bash
pnpm --filter @workspace/mcp-server test:v17
pnpm --filter @workspace/mcp-server v17:stdio
```

### Remote shared-token acceptance

```bash
WOW_ACTION_API_KEY='server-side-only' \
WOW_MCP_AUTH_MODE=shared-token \
WOW_MCP_SHARED_TOKEN='acceptance-token' \
pnpm --filter @workspace/mcp-server v17:http
```

### OAuth production candidate

```bash
WOW_ACTION_API_KEY='server-side-only' \
WOW_MCP_AUTH_MODE=oauth \
WOW_MCP_PUBLIC_URL='https://candidate.example' \
WOW_MCP_OAUTH_ISSUER='https://PROJECT.supabase.co/auth/v1' \
WOW_MCP_OAUTH_RESOURCE='https://candidate.example/mcp' \
WOW_MCP_OAUTH_JWKS_URL='https://PROJECT.supabase.co/auth/v1/.well-known/jwks.json' \
WOW_MCP_OAUTH_SCOPES='openid email' \
WOW_MCP_OAUTH_ALLOWED_SUBJECTS='approved-user-uuid' \
WOW_MCP_OAUTH_PERMISSION_CLAIM='wow_mcp_permissions' \
pnpm --filter @workspace/mcp-server v17:http
```

Endpoints:

- `GET /healthz` - gateway health only; never exposes secret values
- `GET /.well-known/oauth-protected-resource` - OAuth protected-resource metadata
- `GET /.well-known/oauth-protected-resource/mcp` - path-aware OAuth protected-resource metadata for `/mcp`
- `/mcp` - MCP Streamable HTTP transport

`WOW_MCP_AUTH_MODE=disabled` is for isolated CI/protocol tests only and must not be used on an internet-accessible service.

## Authentication promotion boundary

The shared-token mode is an **acceptance scaffold**, not the final ChatGPT production authentication design. The production candidate uses OAuth resource-server validation and fails closed if its OAuth configuration is incomplete.

The gateway validates all of the following before accepting an OAuth MCP request:

- asymmetric JWT signature from the configured JWKS (`RS256` or `ES256` only);
- exact authorization-server issuer;
- exact MCP resource in `aud`;
- `exp` and optional `nbf`;
- stable `sub` on an explicit server-side subject allowlist;
- non-empty OAuth `client_id`;
- operation-specific V17 permission from the signed `wow_mcp_permissions` claim;
- the same OAuth principal for the lifetime of one MCP session.

### Supabase scope limitation

Supabase Auth currently supports standard OAuth/OIDC identity scopes such as `openid`, `email`, `profile`, and `phone`; it does **not** support arbitrary custom OAuth scopes for the V17 permission names. Therefore the strings below are **permissions, not advertised OAuth scopes**:

```text
wow.runtime.read
wow.governance.read
wow.evidence.read
wow.predictions.read
wow.predictions.score
wow.daily.run
wow.recommendations.write
wow.settlements.write
```

The intended Supabase integration is:

1. enable the Supabase OAuth 2.1 server;
2. use Authorization Code + PKCE and the standard `openid email` identity scopes;
3. enable a Custom Access Token Hook that changes OAuth-client tokens only;
4. for an explicitly authorized user subject, set `aud` to the exact MCP resource and add the least-privilege `wow_mcp_permissions` signed claim;
5. leave ordinary non-OAuth application tokens unchanged;
6. keep gateway-side subject, audience, client, signature, lifetime, and permission validation enabled.

The gateway advertises only scopes the authorization server actually supports. It never labels `wow.*` permissions as OAuth scopes.

`WOW_ACTION_API_KEY` must remain a secret on the MCP server and must never be returned to ChatGPT, committed to GitHub, placed in a Skill file, or written into logs.

## Current promotion blockers

The code path is intentionally separable from account-level configuration. As of the current migration work:

- the canonical WOW Supabase project's OAuth server is not yet enabled;
- the Auth/OAuth toggle and Custom Access Token Hook activation require account/dashboard configuration not exposed by the current engineering connector;
- a fresh immutable Render candidate must receive `WOW_ACTION_API_KEY` through server-side secret configuration before backend-authenticated live scoring can be accepted;
- the target ChatGPT workspace must support every required stateful/write MCP operation.

These are promotion blockers, not reasons to weaken authentication or bypass V17 governance.

## Acceptance gates before production GPT migration

All gates are mandatory:

1. CI proves the MCP package loads and exposes exactly the 16 governed operation IDs above.
2. Streamable HTTP `/mcp` initializes successfully on the deployed candidate gateway.
3. Anonymous/invalid OAuth callers fail closed; protected-resource metadata, issuer, audience, lifetime, subject, client, session binding, and signed V17 permission enforcement are verified.
4. `getWowV17BackendHealth` reaches the production backend and returns `can_execute=false`.
5. A known valid one-row prop scores through `scoreWowPickRequest` and its immutable receipt is recovered by `lookupWowV17PredictionReceipts`.
6. A four-row board reconciles one terminal result per input row with stable row identities and no duplicate prediction receipt.
7. A simulated/persisted-response timeout proves receipt recovery occurs before any retry.
8. A best-side prop with no supplied direction reconciles MORE and LESS independently through the governed host workflow.
9. Daily runs in `COMPACT`; full row detail is retrieved separately through `readWowV17DailySnapshotRowDetail`.
10. A team/event request routes through `scoreWowV17TeamEventFromWowHost` and LLP rather than the prop lane.
11. Missing fitted artifact, insufficient model inputs, unsupported/OOD line, malformed model output/package, scorer failure, market/provider failure, and receipt-service failure retain distinct V17 typed outcomes.
12. Recommendation publication remains write-before-display and requires the backend's `display_authorized=true` result.
13. Tool-registry inspection proves there is no wager/order execution tool and `can_execute=false` remains invariant.
14. Familiar and adversarial prompts are compared between the existing production GPT and the candidate plugin for routing, typed failures, row reconciliation, terminal state, and publication behavior.
15. The target ChatGPT workspace supports every required stateful/write MCP operation.
16. Only after all gates pass may the production GPT migration be authorized.

## Deployment rule

Do **not** replace or migrate the production WOW GPT merely because this code merges. Code merge, remote gateway deployment, plugin installation, and production GPT migration are separate promotion stages.

A candidate gateway may be deployed for acceptance once CI is green and its backend credential and authentication boundary are safely provisioned. Production GPT migration remains blocked until the full acceptance matrix above is complete.
