# WOW Betting Engine — V17 Custom GPT editor attestation

Status: `HISTORICAL_LIVE_EDITOR_SYNC_VERIFIED__CURRENT_RESYNC_REQUIRED_AFTER_PR617`

Historical verification date: 2026-09-16
Current reconciliation date: 2026-09-20

This record preserves the last verified live `WOW_BETTING_ENGINE` editor acceptance while explicitly separating that historical evidence from the current required editor resynchronization. It must not be used to claim that the live editor is currently synchronized after P0-D and PR #617.

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

## Historical save/reload acceptance

On 2026-09-16 the live editor was saved, reloaded, and a real Action health invocation succeeded. The editor then reported 5,418 instruction characters, below the 8,000-character product limit. The live Action reached the production Render origin with Bearer authentication and preserved `can_execute=false`.

That evidence is historical only. Subsequent repository changes modified the required large-board orchestration contract and PR #617 repaired canonical prop Action operation IDs. A fresh editor save/reload acceptance is therefore required.

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

## Current live-editor status

The current repository/runtime repair does **not** prove that the live product editor has been re-saved/reloaded with the repaired schema and latest semantic instructions.

```text
LIVE_GPT_EDITOR_SYNC = RESYNC_REQUIRED_AFTER_PR617
REPOSITORY_GOVERNANCE = PR617_MERGED
BACKEND_RUNTIME = separate state
MODEL_CAPABILITY = route-specific separate state
can_execute = false
```

## Acceptance required to re-attest VERIFIED

A new live editor attestation may set `LIVE_GPT_EDITOR_SYNC=VERIFIED` only after all of the following are observed from the actual GPT editor:

1. current instructions are saved and survive reload;
2. the current canonical V17 Action schema is installed;
3. `scoreWowPickRequest` is visible/callable;
4. Bearer auth succeeds using the existing `WOW_ACTION_API_KEY` without exposing or replacing it;
5. a live `/health` Action invocation reaches the production Render backend;
6. required V17 diagnostic Actions remain callable; and
7. `can_execute=false` remains true.

Until then, preserve the 2026-09-16 verification as historical evidence only and do not represent it as current live-editor synchronization.
