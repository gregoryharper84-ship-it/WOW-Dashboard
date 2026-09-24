# WOW Betting Engine — V17 Custom GPT editor attestation

Status: `RESYNC_REQUIRED_AFTER_PR766__LIVE_ACTION_ACCEPTANCE_REQUIRED`

Historical verification date: 2026-09-16
Post-PR617 editor update reported: 2026-09-20
PR654 repository repair merged: 2026-09-21
PR766 host-contract repair: 2026-09-23
Editor 8k installation repair: 2026-09-24
Current reconciliation date: 2026-09-24

This record preserves the last fully verified live `WOW_BETTING_ENGINE` editor acceptance while separately recording later repository changes. The 2026-09-20 editor save predates later host-contract changes, so it cannot prove current instruction parity.

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

## Current canonical repository contract

```text
schema = artifacts/wow-engine/v17/openapi.wow-betting-engine.v17.yaml
run_control_schema = artifacts/wow-engine/v17/openapi.wow-betting-engine.v17.run-control.yaml
instructions = artifacts/wow-engine/WOW_V17_CUSTOM_GPT_INSTRUCTIONS.txt
prizepicks_addendum_source = artifacts/wow-engine/WOW_V17_CUSTOM_GPT_PRIZEPICKS_SKILL_ADDENDUM.txt
prizepicks_addendum_installation_surface = KNOWLEDGE_FILE
editor_instruction_limit = 8000 characters
server = https://wow-governed-probability-engine.onrender.com
auth = API Key / Bearer using existing WOW_ACTION_API_KEY
/score-prop operationId = scoreWowProp
/score-pick-request operationId = scoreWowPickRequest
/v17/prediction-receipts/lookup operationId = lookupWowV17PredictionReceipts
can_execute = false
```

The canonical instructions file is the only repository text pasted into the Custom GPT Instructions field and must remain <=8,000 characters. The PrizePicks addendum is attached as Knowledge instead of being appended to the Instructions field. The deterministic sync builder now fails closed if the instructions exceed the editor limit and emits the addendum separately as `WOW_V17_PRIZEPICKS_HOST_CONTRACT_KNOWLEDGE.txt`.

This installation split changes only editor packaging. It does not weaken or alter V17 routing, sporting-model authority, probability mathematics, calibration, publication governance, typed failures, terminal authority, or execution posture.

PR #654 requires fail-fast Action-surface preflight before large-board row preparation. PR #766 requires complete multi-page PrizePicks source reconciliation before scoring/ranking: every page must be inspected, an initially unreadable page must receive page-level render/screenshot fallback, unresolved pages remain typed `SOURCE_PAGE_UNREADABLE:<page_number>`, and the host may not claim full-board/zero-omission completion while unknown rows can remain. The PrizePicks table contract also requires distinct `Player`, `Matchup`, `PrizePicks line`, `Offer`, `Available side(s)`, and `Current/live note` columns.

## Current live-editor status

```text
LIVE_GPT_EDITOR_SYNC = RESYNC_REQUIRED_AFTER_PR766__LIVE_ACTION_ACCEPTANCE_REQUIRED
BACKEND_RUNTIME = separately verified production state
MODEL_CAPABILITY = route-specific separate state
can_execute = false
```

Repository state, CI success, and backend health do not prove that a production Custom GPT conversation has the newest host contract or canonical Action operations bound to its tool surface.

## Acceptance required to re-attest VERIFIED

A new live editor attestation may set `LIVE_GPT_EDITOR_SYNC=VERIFIED` only after all of the following are observed after:

1. pasting the current canonical instructions into the Instructions field;
2. attaching the current PrizePicks addendum as Knowledge;
3. saving/reloading both canonical Action schemas with existing Bearer auth preserved; and
4. opening a fresh WOW chat.

Fresh-chat acceptance must prove:

1. `getWowV17BackendHealth` is visible and a live `/health` Action invocation reaches the production Render backend;
2. Bearer auth succeeds using the existing `WOW_ACTION_API_KEY` without exposing or replacing it;
3. `scoreWowPickRequest` is visible, callable, and returns a typed governed response/receipt for a known pregame directional row;
4. `lookupWowV17PredictionReceipts` is visible, callable, and recovers that exact immutable receipt;
5. a multi-page PrizePicks PDF is inspected page by page and cannot silently degrade to page 1 if a later page initially fails to render;
6. if a page remains unreadable after fallback, `SOURCE_PAGE_UNREADABLE:<page_number>` plus page reconciliation is preserved and no false `omitted rows = 0` claim appears;
7. required V17 diagnostic Actions remain callable; and
8. `can_execute=false` remains true.

Until then, preserve the historical verification only as historical evidence. Do not represent current live Action binding, current editor parity, or the multi-page PrizePicks contract as complete.
