# WOW Betting Engine — V17 Custom GPT editor attestation

Status: `RESYNC_REQUIRED_AFTER_PR766__LIVE_ACTION_ACCEPTANCE_REQUIRED`

Historical verification date: 2026-09-16
Post-PR617 editor update reported: 2026-09-20
PR654 repository repair merged: 2026-09-21
PR766 host-contract repair: 2026-09-23
Current reconciliation date: 2026-09-23

This record preserves the last fully verified live `WOW_BETTING_ENGINE` editor acceptance while separately recording later repository changes. The 2026-09-20 editor save predates PR #654 and PR #766, so it cannot prove current instruction parity. Full current verification requires the latest canonical host instructions and PrizePicks live-host addendum to be saved/reloaded in the production WOW editor, followed by authenticated Action and multi-page source-ingestion acceptance from a fresh production WOW chat.

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

On 2026-09-20 a later schema/editor update was user-confirmed: `scoreWowPickRequest` was visible, the production Action origin remained configured, Bearer configuration was preserved, the GPT update was saved, and a fresh chat was opened. That remains historical configuration evidence only because later repository host contracts changed.

## Current canonical repository contract

```text
schema = artifacts/wow-engine/v17/openapi.wow-betting-engine.v17.yaml
instructions = artifacts/wow-engine/WOW_V17_CUSTOM_GPT_INSTRUCTIONS.txt
prizepicks_addendum = artifacts/wow-engine/WOW_V17_CUSTOM_GPT_PRIZEPICKS_SKILL_ADDENDUM.txt
server = https://wow-governed-probability-engine.onrender.com
auth = API Key / Bearer using existing WOW_ACTION_API_KEY
/score-prop operationId = scoreWowProp
/score-pick-request operationId = scoreWowPickRequest
/v17/prediction-receipts/lookup operationId = lookupWowV17PredictionReceipts
can_execute = false
```

PR #654 requires a fail-fast Action-surface preflight before large-board row preparation. PR #766 additionally requires complete multi-page PrizePicks source reconciliation before scoring/ranking: every page must be inspected, an initially unreadable page must receive the available page-level render/screenshot fallback, unresolved pages remain typed `SOURCE_PAGE_UNREADABLE:<page_number>`, and the host may not claim full-board/zero-omission completion while unknown rows can remain.

The PrizePicks table contract also requires distinct `Player`, `Matchup`, `PrizePicks line`, `Offer`, `Available side(s)`, and `Current/live note` columns.

## Current live-editor status

```text
LIVE_GPT_EDITOR_SYNC = RESYNC_REQUIRED_AFTER_PR766__LIVE_ACTION_ACCEPTANCE_REQUIRED
REPOSITORY_GOVERNANCE = CURRENT_MAIN_AFTER_PR766_WHEN_MERGED
BACKEND_RUNTIME = separately verified production state
MODEL_CAPABILITY = route-specific separate state
can_execute = false
```

Repository state, CI success, and backend health do not prove that a production Custom GPT conversation has the newest host contract or canonical Action operations bound to its tool surface.

## Acceptance required to re-attest VERIFIED

A new live editor attestation may set `LIVE_GPT_EDITOR_SYNC=VERIFIED` only after all of the following are observed after saving/reloading the current canonical instructions plus PR #766 PrizePicks addendum and opening a fresh WOW chat:

1. `getWowV17BackendHealth` is visible and a live `/health` Action invocation reaches the production Render backend;
2. Bearer auth succeeds using the existing `WOW_ACTION_API_KEY` without exposing or replacing it;
3. `scoreWowPickRequest` is visible, callable, and returns a typed governed response/receipt for a known pregame directional row;
4. `lookupWowV17PredictionReceipts` is visible, callable, and recovers that exact immutable receipt;
5. a multi-page PrizePicks PDF is inspected page by page and cannot silently degrade to page 1 if a later page initially fails to render;
6. if a page remains unreadable after fallback, `SOURCE_PAGE_UNREADABLE:<page_number>` plus page reconciliation is preserved and no false `omitted rows = 0` claim appears;
7. required V17 diagnostic Actions remain callable; and
8. `can_execute=false` remains true.

Until then, preserve the 2026-09-16 verification and 2026-09-20 update only as historical evidence. Do not represent current live Action binding, PR #766 editor parity, or the multi-page PrizePicks contract as complete.
