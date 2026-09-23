# WOW V17 production status

Updated: 2026-09-23

This is the current-status pointer for the governed WOW V17 system. Historical proposal, migration, review, and incident documents remain archival and must not override this file when they describe an older lifecycle state.

## Current state

- Runtime: **V17 ACTIVE** when confirmed by production health.
- Production service: `wow-governed-probability-engine`.
- Production branch: `main`.
- Current live production SHA/deploy fields below are deployment attestations and must be reconciled from an exact Render receipt; a green deployment workflow conclusion alone is not proof of deployment.
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
- `MODEL_UNAVAILABLE` is reserved for true controlling-model capability absence. Input, scorer, output, host-Action, market, source-ingestion, and publication failures retain their own typed semantics.
- Missing market evidence must not erase a completed sporting probability when the backend contract preserves it.
- `can_execute=false` remains invariant.

## Production route verification

Direct production probing has established that `/score-pick-request` is mounted and bearer-protected. Exact deployment state is proven only by the governed Render receipt for the target protected-main SHA; non-main and stale-SHA workflow runs may conclude successfully while correctly emitting a typed no-deploy receipt.

Production health/runtime verification remain separate from live GPT editor acceptance and route-specific model capability.

## Repository governance

The canonical V17 Action contract is `artifacts/wow-engine/v17/openapi.wow-betting-engine.v17.yaml` and advertises:

- `/score-prop` -> `scoreWowProp`
- `/score-pick-request` -> `scoreWowPickRequest`

Legacy `scoreWowV17Prop` / `scoreWowV17PickRequest` identifiers remain compatibility aliases only where backend host routing explicitly accepts them.

Protected-main work includes the governed Scout publication boundary, current production canaries, live Action-surface preflight, and the PrizePicks multi-page ingestion contract. The current team/event publication guard fails closed unless the row proves publishability, rank eligibility, calibrated probability/lower-bound package, terminal approval, probability audit, event mutex, correct terminal authority, and `can_execute=false`.

## Custom GPT editor synchronization

**LIVE_GPT_EDITOR_SYNC = RESYNC_REQUIRED_AFTER_PR766__LIVE_ACTION_ACCEPTANCE_REQUIRED.**

Historical live editor evidence remains valid: a production save/reload + Action health acceptance was verified on 2026-09-16, and a later editor/schema update was user-confirmed on 2026-09-20. Those events predate subsequent repository host-contract changes and therefore do not prove current editor parity.

PR #766 changes `WOW_V17_CUSTOM_GPT_PRIZEPICKS_SKILL_ADDENDUM.txt` so a multi-page PDF must be reconciled page by page, a failed first render must receive the available page-level fallback, unreadable pages remain typed source-ingestion blockers, false `omitted rows = 0` completion is prohibited, and the user-facing prop table keeps distinct columns.

Repository merge/CI cannot save the OpenAI Custom GPT editor. Current editor parity becomes `VERIFIED` only after the current canonical instructions plus PrizePicks addendum are saved/reloaded in the production WOW editor and a fresh production chat proves `/health`, `scoreWowPickRequest`, immutable receipt lookup, the multi-page ingestion contract, required diagnostics, Bearer auth, and `can_execute=false`.

Authoritative detail: `artifacts/wow-engine/V17_CUSTOM_GPT_EDITOR_SYNC.md`.

## User-journey health

**USER_JOURNEY_HEALTH = FAIL — NO_END_TO_END_GOVERNED_PROP_RESULT.**

The golden production acceptance prompt remains:

```text
Use full model and provide me the best props across all sports.
```

`USER_JOURNEY_HEALTH` becomes PASS only after the production ChatGPT path invokes the live `scoreWowPickRequest` Action, acquires current prop inventory with explicit source typing, routes supported rows through the correct fitted specialists, preserves exact typed failures, returns governed probability/calibration/lower-bound packages where applicable, passes through `V17_TERMINAL_REDUCER`, and hands the actual governed result back to ChatGPT.

Green CI, backend health, route-mounted evidence, repository addendum changes, historical editor evidence, Scout discovery, or a local reconstruction cannot independently set this status to PASS.

Authoritative detail: `artifacts/wow-engine/V17_USER_JOURNEY_HEALTH.md`.

## Status language

Report these independently:

- `BACKEND_RUNTIME = V17 ACTIVE` when production health confirms it.
- `MODEL_CAPABILITY = route-specific backend result`.
- `REPOSITORY_GOVERNANCE = current protected-main CI state`.
- `LIVE_GPT_EDITOR_SYNC = RESYNC_REQUIRED_AFTER_PR766__LIVE_ACTION_ACCEPTANCE_REQUIRED` until editor resync plus live Action/multi-page acceptance passes.
- `USER_JOURNEY_HEALTH = FAIL` until the golden production canary passes.
