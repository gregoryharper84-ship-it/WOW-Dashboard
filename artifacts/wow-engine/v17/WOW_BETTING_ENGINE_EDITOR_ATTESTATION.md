# WOW Betting Engine — V17 Custom GPT editor attestation

Status: `RESYNC_REQUIRED_AFTER_PR654__LIVE_ACTION_ACCEPTANCE_REQUIRED`

Historical verification date: 2026-09-16
Post-PR617 editor update reported: 2026-09-20
PR654 repository repair merged: 2026-09-21
Current reconciliation date: 2026-09-21

This record preserves the last fully verified live `WOW_BETTING_ENGINE` editor acceptance while separately recording later editor and repository changes. PR #654 changed the canonical host instructions after the 2026-09-20 editor save, so that prior editor update no longer proves current instruction parity. Full current verification requires the PR654 instruction source to be saved/reloaded in the production WOW editor and a post-save authenticated Action acceptance from a fresh production WOW chat.

## Historical live editor identity and safety

```text
custom_gpt_name = WOW Betting Engine
custom_gpt_identity = WOW_BETTING_ENGINE
host_role = PLAYER_PROP_AND_SCALAR_INTELLIGENCE
nested_custom_gpt_required = false
can_execute = false
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS = true
```

WOW owns player/scalar props. Team/event winner/favorite/underdog/upset objectives route to `LLP_TEAM_BETTING_ENGINE`. Scout/Research remain evidence-only. Exactly one controlling fitted specialist owns each row/event.

## Historical verified acceptance

On 2026-09-16 the live editor was saved, reloaded, and a real Action health invocation succeeded. The live Action reached the production Render origin with Bearer authentication and preserved `can_execute=false`.

Subsequent repository changes modified the required large-board orchestration contract and PR #617 repaired canonical prop Action operation IDs, so that 2026-09-16 verification cannot alone prove current parity.

## Current canonical repository contract

```text
schema = artifacts/wow-engine/v17/openapi.wow-betting-engine.v17.yaml
instructions = artifacts/wow-engine/WOW_V17_CUSTOM_GPT_INSTRUCTIONS.txt
server = https://wow-governed-probability-engine.onrender.com
auth = API Key / Bearer using existing WOW_ACTION_API_KEY
/score-prop operationId = scoreWowProp
/score-pick-request operationId = scoreWowPickRequest
/v17/prediction-receipts/lookup operationId = lookupWowV17PredictionReceipts
can_execute = false
```

The current semantic host instructions require LIVE_GPT interactive scoring in <=4 directional rows per Action call, immutable receipt recovery before retry after ambiguous completion, exact-once reconciliation, and no ranking of partial pools.

PR #654 additionally requires a preflight before large-board row preparation. The live GPT Action surface must expose all three operations:

```text
getWowV17BackendHealth
scoreWowPickRequest
lookupWowV17PredictionReceipts
```

If any are absent, the host must stop before row preparation with:

```text
LIVE_GPT_ACTION_INVOCATION_BLOCKED
rows_attempted = 0
scoring_attempted = false
backend_model_capability = UNKNOWN
can_execute = false
```

This is a host/session Action-binding failure, not `MODEL_UNAVAILABLE` and not a sporting-model result.

## Post-PR617 editor update record

User-confirmed on 2026-09-20:

- the pinned canonical V17 Action schema was imported into the production WOW editor;
- `scoreWowPickRequest` was present in the Action surface;
- the production Render Action origin remained configured;
- the existing Bearer authentication configuration was preserved and not replaced;
- the editor update was saved; and
- a fresh WOW chat was opened to bind the updated Action schema.

That update resolved `RESYNC_REQUIRED_AFTER_PR617` at the time, but it predates PR #654's canonical instruction change and therefore is now historical configuration evidence rather than current parity evidence.

## PR654 repository repair

PR #654 (`fix(v17): fail fast when live GPT prop Actions are not bound`) merged to protected `main` on 2026-09-21.

It adds the deterministic live Action-surface preflight, preserves the typed `LIVE_GPT_ACTION_INVOCATION_BLOCKED` host failure, prevents hundreds of board rows from being prepared before discovering an unbound Action surface, and does not alter sporting probabilities, calibration, terminal authority, or execution capability.

## Current live-editor status

```text
LIVE_GPT_EDITOR_SYNC = RESYNC_REQUIRED_AFTER_PR654__LIVE_ACTION_ACCEPTANCE_REQUIRED
REPOSITORY_GOVERNANCE = CURRENT_MAIN
BACKEND_RUNTIME = separately verified production state
MODEL_CAPABILITY = route-specific separate state
can_execute = false
```

Repository state, CI success, and backend health do not prove that a production Custom GPT conversation has the canonical Action operations bound to its tool surface.

## Acceptance required to re-attest VERIFIED

A new live editor attestation may set `LIVE_GPT_EDITOR_SYNC=VERIFIED` only after all of the following are observed after saving/reloading the PR654 canonical instructions in the production WOW editor and opening a fresh WOW chat:

1. `getWowV17BackendHealth` is visible and a live `/health` Action invocation reaches the production Render backend;
2. Bearer auth succeeds using the existing `WOW_ACTION_API_KEY` without exposing or replacing it;
3. `scoreWowPickRequest` is visible, callable, and returns a typed governed response/receipt for a known pregame directional row;
4. `lookupWowV17PredictionReceipts` is visible, callable, and recovers that exact immutable receipt;
5. required V17 diagnostic Actions remain callable; and
6. `can_execute=false` remains true.

Until then, preserve the 2026-09-16 verification as historical fully verified evidence, preserve the 2026-09-20 editor update as historical configuration evidence, and do not represent current live Action binding or PR654 editor parity as complete.
