# WOW V17 Actions -> MCP/Plugin Migration

Status: `IMPLEMENTATION / ACCEPTANCE_PENDING`

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
        | OAuth 2.1 + PKCE in the final production integration
        v
WOW V17 MCP gateway
        |
        | Authorization: Bearer $WOW_ACTION_API_KEY
        | credential remains server-side only
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

| MCP tool | Backend route | State |
| --- | --- | --- |
| `getWowV17BackendHealth` | `GET /health` | read-only |
| `getWowV17Governance` | `GET /governance` | read-only |
| `getWowV17HostContract` | `GET /v17/host-contract` | read-only |
| `getWowV17DetailedEvidenceContract` | `GET /v17/detailed-evidence-contract` | read-only |
| `getWowV17Capabilities` | `GET /v17/capabilities` | read-only |
| `getWowV17RundownMarketHealth` | `GET /v17/market-health/rundown` | read-only |
| `getWowV17OddsApiMarketHealth` | `GET /v17/market-health/odds-api` | read-only |
| `getWowV17CompactEspnDiscovery` | `GET /v17/discovery/espn-compact` | read-only |
| `scoreWowPickRequest` | `POST /score-pick-request` | stateful |
| `scoreWowProp` | `POST /score-prop` | stateful |
| `runWowV17DailySnapshot` | `POST /v17/daily-snapshot-run` | stateful |
| `readWowV17DailySnapshotRowDetail` | `GET /v17/daily-snapshot-run/{run_id}/rows` | read-only |
| `scoreWowV17TeamEventFromWowHost` | `POST /score-team-event` | stateful |
| `lookupWowV17PredictionReceipts` | `POST /v17/prediction-receipts/lookup` | read-only |
| `recordWowV17Recommendations` | `POST /record-recommendations` | stateful |
| `settleWowV17Recommendations` | `POST /settle-recommendations` | stateful |

There is deliberately no tool for placing, approving, modifying, routing, canceling, or executing a wager or market order.

The existing `V17_OPERATIONS` map remains the sole MCP operation-permission map:

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

### Remote Streamable HTTP acceptance

```bash
WOW_ACTION_API_KEY='server-side-only' \
WOW_MCP_AUTH_MODE=shared-token \
WOW_MCP_SHARED_TOKEN='acceptance-token' \
pnpm --filter @workspace/mcp-server v17:http
```

Endpoints:

- `GET /healthz` - gateway health only; never exposes secret values
- `GET /.well-known/oauth-protected-resource` - compatibility protected-resource metadata endpoint
- `GET /.well-known/oauth-protected-resource/mcp` - path-specific protected-resource metadata for `/mcp`
- `/mcp` - MCP Streamable HTTP transport

`WOW_MCP_AUTH_MODE=disabled` is for isolated CI/protocol tests only and must not be used on an internet-accessible service.

## Authentication promotion boundary

The shared-token mode is an **acceptance scaffold**, not the final ChatGPT production authentication design.

Before the production WOW GPT is migrated, the remote MCP gateway must be bound as an OAuth 2.1 protected resource suitable for ChatGPT, including Authorization Code + PKCE, protected-resource metadata, authorization-server metadata, asymmetric JWT validation, issuer/audience/expiry validation, explicit OAuth client allowlisting, refresh-token support, and least-privilege operation authorization.

The gateway supports `WOW_MCP_AUTH_MODE=oauth` as the protected-resource side of that design. It validates RS256/ES256 access-token signatures against the configured JWKS, requires an audience restricted to the exact protected MCP resource, binds 2025-era MCP sessions to the authenticated subject/client/session tuple, and maps each `tools/call` request to the existing `V17_OPERATIONS[*].scope` permission. An unknown JWT `kid` forces one JWKS refresh before the request fails closed, so signing-key rotation does not depend on cache expiry.

The final ChatGPT OAuth acceptance must verify that the authorization server accepts Authorization Code + PKCE with `S256`, supports a ChatGPT-compatible client-registration mode, returns refresh tokens, and mints a token whose `aud` identifies the exact WOW MCP protected resource. A generic Supabase `aud=authenticated` token is not acceptable for production WOW MCP.

### Supabase OAuth provider note

Supabase Auth can act as an OAuth 2.1 / OIDC authorization server and supports Authorization Code + PKCE, refresh tokens, dynamic client registration, discovery, JWKS, and Custom Access Token Hooks. As of this implementation, Supabase's OAuth server supports the standard OIDC scopes (`openid`, `email`, `profile`, `phone`) but does **not** support arbitrary custom OAuth scopes.

That provider limitation must not silently weaken WOW authorization. When Supabase is the authorization server:

- standard OIDC scopes are used only for OAuth/OIDC identity data;
- WOW operation authorization is carried in the signed server-controlled `app_metadata.wow_permissions` access-token claim;
- the claim is populated only for explicitly approved OAuth `client_id` values by the reviewed Custom Access Token Hook candidate;
- an approved client is also bound to an exact HTTPS `resource_audience`, and the hook changes `aud` from the generic Supabase audience to that protected resource;
- an unknown or disabled client receives an empty WOW permission set and keeps the ordinary Supabase audience, so it cannot satisfy the WOW resource-server audience check;
- the MCP gateway also honors native `scope` / `scp` permission claims if a future authorization server supports the WOW permission names directly;
- `user_metadata` is never used for authorization.

The reviewed Supabase claim candidate is stored at:

`artifacts/wow-engine/v17/sql/20260922_v17_mcp_oauth_permission_claim.sql`

Creating that database function alone does not activate it. Supabase Auth Hook configuration, OAuth Server enablement, authorization UI/path configuration, and OAuth client approval are separate promotion actions.

`WOW_ACTION_API_KEY` must remain a secret on the MCP server and must never be returned to ChatGPT, committed to GitHub, placed in a Skill file, or written into logs.

## Protocol-version promotion note

The current acceptance server is still built on the TypeScript MCP SDK v1 line and therefore exercises the established initialize/session era. MCP `2026-07-28` is now the current protocol and the TypeScript SDK v2 line can serve both the new stateless era and legacy 2025-era traffic. Production cutover must therefore prove one of the following rather than assume compatibility:

- the target ChatGPT plugin connection negotiates the current v1 server successfully through its supported legacy path; or
- a separately tested v2 transport upgrade passes the same 16-tool parity, exact-once, typed-failure, authorization, and regression gates before cutover.

Do not combine an SDK-major/protocol rewrite with sporting probability changes.

## Acceptance gates before production GPT migration

All gates are mandatory:

1. CI proves the MCP package loads and exposes exactly the 16 governed operation IDs above.
2. Streamable HTTP `/mcp` initializes successfully on the deployed candidate gateway, and the target ChatGPT client proves a supported protocol negotiation path rather than relying on an assumed legacy fallback.
3. Auth fails closed for anonymous/invalid callers; OAuth JWT signature/issuer/resource-audience/expiry/client validation and least-privilege WOW operation authorization are verified. If the authorization server supports native custom scopes, verify those scopes directly. If it does not, verify the signed server-controlled permission claim and do not misrepresent standard OIDC scopes as WOW authorization.
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
15. The target ChatGPT workspace/account supports every required stateful/write MCP operation and its confirmation behavior is compatible with WOW's governed state changes.
16. Only after all gates pass may the production GPT migration be authorized.

## Deployment rule

Do **not** replace or migrate the production WOW GPT merely because this code merges. Code merge, remote gateway deployment, OAuth provider provisioning, plugin installation, and production GPT migration are separate promotion stages.

A candidate gateway may be deployed for acceptance once CI is green and its backend credential and authentication boundary are safely provisioned. Production GPT migration remains blocked until the full acceptance matrix above is complete.
