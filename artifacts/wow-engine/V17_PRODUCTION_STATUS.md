# WOW V17 production status

Updated: 2026-09-21

This is the current-status pointer for the governed WOW V17 system. Historical proposal, migration, review, and incident documents remain archival and must not override this file when they describe an older lifecycle state.

## Current state

- Runtime: **V17 ACTIVE** when confirmed by production health.
- Production service: `wow-governed-probability-engine`.
- Production branch: `main`.
- Current live production SHA: `2d430886dc003b942cf9ca9d0251963cfc88d858`.
- Current live Render deploy: `dep-daoib7rbc2fs73e4vco0`.
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

Direct production probing established that `/score-pick-request` is mounted and bearer-protected. The latest Render deployment for `wow-governed-probability-engine` is live at SHA `2d430886dc003b942cf9ca9d0251963cfc88d858`. Production health and runtime verification remain separate from live GPT editor acceptance and route-specific model capability.

## Repository governance

**PR #617 MERGED.** The canonical V17 Action contract is `artifacts/wow-engine/v17/openapi.wow-betting-engine.v17.yaml` and advertises:

- `/score-prop` -> `scoreWowProp`
- `/score-pick-request` -> `scoreWowPickRequest`

Legacy `scoreWowV17Prop` / `scoreWowV17PickRequest` identifiers remain compatibility aliases only where backend host routing explicitly accepts them.

Subsequent protected-main work includes the governed Scout publication boundary and current production canaries. The current team/event publication guard fails closed unless the row proves publishability, rank eligibility, calibrated probability/lower-bound package, terminal approval, probability audit, event mutex, correct terminal authority, and `can_execute=false`.

## Custom GPT editor synchronization

**LIVE_GPT_EDITOR_SYNC = EDITOR_UPDATED__LIVE_ACTION_ACCEPTANCE_REQUIRED.**

The stale `RESYNC_REQUIRED_AFTER_PR617` state is retired. On 2026-09-20 the production WOW editor update was user-confirmed: the pinned canonical V17 schema was imported, `scoreWowPickRequest` was visible, the production server target remained configured, the existing Bearer authentication configuration was preserved, the GPT update was saved, and a fresh WOW chat was opened.

That proves the editor update was performed, but it does not yet prove the post-update live Action acceptance. `LIVE_GPT_EDITOR_SYNC` becomes `VERIFIED` only after the fresh production WOW chat supplies live evidence that `/health` reaches the production backend through the installed Action, Bearer auth succeeds, `scoreWowPickRequest` is callable and returns a typed governed response/receipt, required diagnostics remain callable, and `can_execute=false` remains true.

Current live-host requirements continue to include:

- canonical schema: `v17/openapi.wow-betting-engine.v17.yaml`;
- canonical prop operation: `scoreWowPickRequest`;
- API Key/Bearer auth using the existing `WOW_ACTION_API_KEY`;
- LIVE_GPT interactive scoring in <=4 directional rows per Action call;
- immutable receipt lookup before retry after timeout/disconnect/ambiguous completion;
- exact-once reconciliation and no ranking of partial pools; and
- `can_execute=false` unchanged.

Authoritative detail: `artifacts/wow-engine/V17_CUSTOM_GPT_EDITOR_SYNC.md`.

## User-journey health

**USER_JOURNEY_HEALTH = FAIL — NO_END_TO_END_GOVERNED_PROP_RESULT.**

The golden production acceptance prompt remains:

```text
Use full model and provide me the best props across all sports.
```

`USER_JOURNEY_HEALTH` becomes PASS only after the production ChatGPT path invokes the live `scoreWowPickRequest` Action, acquires current prop inventory with explicit source typing, routes supported rows through the correct fitted specialists, preserves exact typed failures, returns governed probability/calibration/lower-bound packages where applicable, passes through `V17_TERMINAL_REDUCER`, and hands the actual governed result back to ChatGPT.

Green CI, backend health, route-mounted evidence, editor-update evidence, Scout discovery, or a local reconstruction cannot independently set this status to PASS.

Authoritative detail: `artifacts/wow-engine/V17_USER_JOURNEY_HEALTH.md`.

## Status language

Report these independently:

- `BACKEND_RUNTIME = V17 ACTIVE` when production health confirms it.
- `MODEL_CAPABILITY = route-specific backend result`.
- `REPOSITORY_GOVERNANCE = current protected-main CI state`.
- `LIVE_GPT_EDITOR_SYNC = EDITOR_UPDATED__LIVE_ACTION_ACCEPTANCE_REQUIRED` until the post-update live Action acceptance passes.
- `USER_JOURNEY_HEALTH = FAIL` until the golden production canary passes.
