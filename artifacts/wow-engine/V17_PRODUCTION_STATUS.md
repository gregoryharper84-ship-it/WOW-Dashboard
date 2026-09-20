# WOW V17 production status

Updated: 2026-09-20

This is the current-status pointer for the governed WOW V17 system. Historical proposal, migration, review, and incident documents remain archival and must not override this file when they describe an older lifecycle state.

## Current state

- Runtime: **V17 ACTIVE** when confirmed by production health.
- Production service: `wow-governed-probability-engine`.
- Production branch: `main`.
- Global terminal reducer: `V17_TERMINAL_REDUCER`.
- `WOW_CAN_EXECUTE=false`.
- `WOW_DRY_RUN_ONLY=true`.
- No wager/order execution path is authorized.

## Routing and probability contract

- WOW_BETTING_ENGINE owns player/prop/scalar intelligence.
- LLP_TEAM_BETTING_ENGINE owns team/event winner/favorite/underdog/upset intelligence.
- Scout and Research gather/reconcile evidence; they are not fitted probability publishers.
- Exactly one controlling specialist owns each row/event.
- Governed probability requires the correct fitted specialist and its valid probability/calibration/bound package.
- `MODEL_UNAVAILABLE` is reserved for true controlling-model capability absence. Input, scorer, output, host-Action, market, and publication failures retain their own typed semantics.
- Missing market evidence must not erase a completed sporting probability when the backend contract preserves it.
- `can_execute=false` remains invariant.

## Production route verification

Direct production probing on 2026-09-20 established that `/score-pick-request` is mounted and bearer-protected: an unauthenticated request returns HTTP 401 for a missing/malformed Authorization header. Production `/health` returns `V17_ACTIVE` and `can_execute=false`.

This route evidence is separate from live GPT editor synchronization and from route-specific model capability.

## Repository governance

**PR #617 MERGED.** The canonical V17 Action contract is `artifacts/wow-engine/v17/openapi.wow-betting-engine.v17.yaml` and now advertises:

- `/score-prop` -> `scoreWowProp`
- `/score-pick-request` -> `scoreWowPickRequest`

Legacy `scoreWowV17Prop` / `scoreWowV17PickRequest` identifiers remain compatibility aliases only where backend host routing explicitly accepts them.

The repair did not change sporting model math, calibration, qualification thresholds, `V17_TERMINAL_REDUCER`, or `can_execute=false`.

## Custom GPT editor synchronization

**LIVE_GPT_EDITOR_SYNC = RESYNC_REQUIRED_AFTER_PR617.**

The 2026-09-16 `LIVE_EDITOR_SYNC_VERIFIED` record is historical evidence only. Subsequent P0-D orchestration changes and PR #617 changed the required live semantic/schema contract, so repository correctness must not be reported as current live-editor synchronization.

Current live-host requirements include:

- canonical schema: `v17/openapi.wow-betting-engine.v17.yaml`;
- canonical prop operation: `scoreWowPickRequest`;
- API Key/Bearer auth using the existing `WOW_ACTION_API_KEY`;
- LIVE_GPT interactive scoring in <=4 directional rows per Action call;
- immutable receipt lookup before retry after timeout/disconnect/ambiguous completion;
- exact-once reconciliation and no ranking of partial pools; and
- `can_execute=false` unchanged.

`LIVE_GPT_EDITOR_SYNC` may return to VERIFIED only after the actual GPT editor is saved, reloaded, and acceptance-tested against this current contract.

Authoritative detail: `artifacts/wow-engine/V17_CUSTOM_GPT_EDITOR_SYNC.md`.

## User-journey health

**USER_JOURNEY_HEALTH = FAIL — NO_END_TO_END_GOVERNED_PROP_RESULT.**

The golden production acceptance prompt remains:

```text
Use full model and provide me the best props across all sports.
```

`USER_JOURNEY_HEALTH` becomes PASS only after the production ChatGPT path invokes the live `scoreWowPickRequest` Action, acquires current prop inventory with explicit source typing, routes supported rows through the correct fitted specialists, preserves exact typed failures, returns governed probability/calibration/lower-bound packages where applicable, passes through `V17_TERMINAL_REDUCER`, and hands the actual governed result back to ChatGPT.

Green CI, backend health, route-mounted evidence, historical editor save/reload, Scout discovery, or a local reconstruction cannot independently set this status to PASS.

Authoritative detail: `artifacts/wow-engine/V17_USER_JOURNEY_HEALTH.md`.

## Status language

Report these independently:

- `BACKEND_RUNTIME = V17 ACTIVE` when production health confirms it.
- `MODEL_CAPABILITY = route-specific backend result`.
- `REPOSITORY_GOVERNANCE = PR617_MERGED / protected-main CI state`.
- `LIVE_GPT_EDITOR_SYNC = RESYNC_REQUIRED_AFTER_PR617`.
- `USER_JOURNEY_HEALTH = FAIL until golden production canary passes`.
