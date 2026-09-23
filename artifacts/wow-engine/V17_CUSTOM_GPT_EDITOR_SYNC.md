# WOW V17 Custom GPT editor synchronization

Updated: 2026-09-23

Status: **RESYNC_REQUIRED_AFTER_PR766__LIVE_ACTION_ACCEPTANCE_REQUIRED**

The production `WOW_BETTING_ENGINE` editor was historically saved/reloaded and Action-tested on 2026-09-16, and a later editor/schema update was user-confirmed on 2026-09-20. Those facts remain valid historical evidence. They do **not** prove current parity because repository host instructions changed afterward (including PR #654), and PR #766 changes the live PrizePicks host addendum again.

## Current repository contract

- Canonical Action schema: `artifacts/wow-engine/v17/openapi.wow-betting-engine.v17.yaml`.
- Canonical host instructions: `artifacts/wow-engine/WOW_V17_CUSTOM_GPT_INSTRUCTIONS.txt`.
- PrizePicks live-host addendum: `artifacts/wow-engine/WOW_V17_CUSTOM_GPT_PRIZEPICKS_SKILL_ADDENDUM.txt`.
- Canonical prop operations:
  - `/score-prop` -> `scoreWowProp`
  - `/score-pick-request` -> `scoreWowPickRequest`
- Authentication remains API Key -> Bearer using the existing `WOW_ACTION_API_KEY`.
- `can_execute=false`, dry-run-only behavior, and `V17_TERMINAL_REDUCER` authority remain binding.

## PR #766 host-contract change

PR #766 makes multi-page PrizePicks ingestion fail closed instead of silently degrading to page 1. The live host must now:

- enumerate and inspect every page of an attached multi-page PDF before scoring/ranking;
- retry a blank, clipped, or illegible page through the available page-level render/screenshot path before declaring it unreadable;
- preserve `SOURCE_PAGE_UNREADABLE:<page_number>` and page-count reconciliation when source ingestion remains incomplete;
- never claim `omitted rows = 0` or full-board reconciliation while unknown rows can remain on unreadable pages;
- treat unreadable source pages as source-ingestion blockers, not `MODEL_UNAVAILABLE`; and
- render distinct `Player`, `Matchup`, `PrizePicks line`, `Offer`, `Available side(s)`, and `Current/live note` columns.

Repository merge/CI does not itself update the OpenAI Custom GPT editor. Therefore current live editor parity must remain fail closed until the PR #766 host contract is saved/reloaded and accepted from a fresh production WOW chat.

## Historical live editor evidence

Historical facts only:

- 2026-09-16: live editor save/reload plus authenticated Action health acceptance succeeded;
- 2026-09-20: canonical schema update was user-confirmed, `scoreWowPickRequest` was visible, the production Render origin remained configured, Bearer configuration was preserved, the GPT was saved, and a fresh chat was opened.

Neither historical event proves parity with repository changes made after 2026-09-20.

## Acceptance required to re-attest VERIFIED

After PR #766 is merged, the production WOW editor must be saved/reloaded with the current canonical instructions plus PrizePicks addendum, then a fresh production chat must prove:

1. `getWowV17BackendHealth` is visible and `/health` reaches the production Render backend;
2. Bearer authentication succeeds without exposing or replacing `WOW_ACTION_API_KEY`;
3. `scoreWowPickRequest` is callable and returns a typed governed response/receipt;
4. `lookupWowV17PredictionReceipts` is callable and can recover the immutable receipt;
5. a multi-page PrizePicks attachment follows the page-completeness contract, including typed unreadable-page behavior if applicable;
6. required V17 diagnostics remain callable; and
7. `can_execute=false` remains true.

Until those checks are observed, report:

```text
LIVE_GPT_EDITOR_SYNC = RESYNC_REQUIRED_AFTER_PR766__LIVE_ACTION_ACCEPTANCE_REQUIRED
USER_JOURNEY_HEALTH = FAIL
can_execute = false
```

Do not report `VERIFIED`, and do not downgrade editor/session synchronization failures into sporting-model failures such as `MODEL_UNAVAILABLE`, `MODEL_INPUTS_INSUFFICIENT`, `MODEL_SCORER_FAILED`, or `MODEL_OUTPUT_INVALID`.
