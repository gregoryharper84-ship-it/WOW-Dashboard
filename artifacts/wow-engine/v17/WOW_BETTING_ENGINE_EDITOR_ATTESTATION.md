# WOW Betting Engine — V17 Custom GPT editor attestation

Status: `CURRENT_EDITOR_UPDATE_REPORTED__LIVE_ACTION_ACCEPTANCE_REQUIRED`

Historical verification date: 2026-09-16
Post-PR617 editor update reported: 2026-09-20
Current reconciliation date: 2026-09-21

This record preserves the last fully verified live `WOW_BETTING_ENGINE` editor acceptance while separately recording the post-PR617 editor update that the user completed on 2026-09-20. The editor is no longer correctly described as "resync not performed". Full current verification still requires a post-update authenticated Action acceptance from the fresh production WOW chat.

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
server = https://wow-governed-probability-engine.onrender.com
auth = API Key / Bearer using existing WOW_ACTION_API_KEY
/score-prop operationId = scoreWowProp
/score-pick-request operationId = scoreWowPickRequest
can_execute = false
```

`scoreWowV17Prop` and `scoreWowV17PickRequest` are compatibility aliases only where explicitly accepted by backend host routing; they are not the canonical live GPT operation names.

The current semantic host instructions require LIVE_GPT interactive scoring in <=4 directional rows per Action call, immutable receipt recovery before retry after ambiguous completion, exact-once reconciliation, and no ranking of partial pools.

## Post-PR617 editor update record

User-confirmed on 2026-09-20:

- the pinned canonical V17 Action schema was imported into the production WOW editor;
- `scoreWowPickRequest` was present in the Action surface;
- the production Render Action origin remained configured;
- the existing Bearer authentication configuration was preserved and not replaced;
- the editor update was saved; and
- a fresh WOW chat was opened to bind the updated Action schema.

This resolves the stale `RESYNC_REQUIRED_AFTER_PR617` status. It is evidence of the editor update, not evidence that the fresh session successfully executed an authenticated Action.

## Current live-editor status

```text
LIVE_GPT_EDITOR_SYNC = EDITOR_UPDATED__LIVE_ACTION_ACCEPTANCE_REQUIRED
REPOSITORY_GOVERNANCE = CURRENT_MAIN
BACKEND_RUNTIME = V17_ACTIVE when production health confirms it
MODEL_CAPABILITY = route-specific separate state
can_execute = false
```

## Acceptance required to re-attest VERIFIED

A new live editor attestation may set `LIVE_GPT_EDITOR_SYNC=VERIFIED` only after all of the following are observed from the fresh production WOW chat:

1. a live `/health` Action invocation reaches the production Render backend;
2. Bearer auth succeeds using the existing `WOW_ACTION_API_KEY` without exposing or replacing it;
3. `scoreWowPickRequest` is actually callable and returns a typed governed response/receipt;
4. required V17 diagnostic Actions remain callable; and
5. `can_execute=false` remains true.

Until then, preserve the 2026-09-16 verification as historical fully verified evidence, preserve the 2026-09-20 editor update as current user-reported configuration evidence, and do not represent the post-PR617 live Action acceptance as complete.
