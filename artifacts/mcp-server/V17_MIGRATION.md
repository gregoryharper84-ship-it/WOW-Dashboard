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
- `/mcp` - MCP Streamable HTTP transport

`WOW_MCP_AUTH_MODE=disabled` is for isolated CI/protocol tests only and must not be used on an internet-accessible service.

## Authentication promotion boundary

The shared-token mode is an **acceptance scaffold**, not the final ChatGPT production authentication design.

Before the production WOW GPT is migrated, the remote MCP gateway must be bound as an OAuth 2.1 protected resource suitable for ChatGPT, including Authorization Code + PKCE, protected-resource metadata, authorization-server metadata, issuer/audience/expiry validation, and least-privilege scope enforcement. The proposed scopes are:

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

`WOW_ACTION_API_KEY` must remain a secret on the MCP server and must never be returned to ChatGPT, committed to GitHub, placed in a Skill file, or written into logs.

## Acceptance gates before production GPT migration

All gates are mandatory:

1. CI proves the MCP package loads and exposes exactly the 16 governed operation IDs above.
2. Streamable HTTP `/mcp` initializes successfully on the deployed candidate gateway.
3. Auth fails closed for anonymous/invalid callers and OAuth scope enforcement is verified.
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
