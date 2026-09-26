# WOW V17 Custom GPT editor synchronization

Updated: 2026-09-26

Status: **RESYNC_REQUIRED_AFTER_ISSUE871_ACTION_SCHEMA_UPDATE__LIVE_ACTION_ACCEPTANCE_REQUIRED**

The production `WOW_BETTING_ENGINE` editor was historically saved/reloaded and Action-tested on 2026-09-16, and a later editor/schema update was user-confirmed on 2026-09-20. Those facts remain historical evidence only; later repository host-contract and Action-contract changes require a fresh live-editor resync and acceptance.

## Current repository contract

- Canonical live Action schema: `artifacts/wow-engine/v17/openapi.wow-betting-engine.v17.yaml`.
- Canonical host instructions: `artifacts/wow-engine/WOW_V17_CUSTOM_GPT_INSTRUCTIONS.txt`.
- PrizePicks live-host addendum: `artifacts/wow-engine/WOW_V17_CUSTOM_GPT_PRIZEPICKS_SKILL_ADDENDUM.txt`.
- Custom GPT Instructions field hard limit: **8,000 characters**; repository safety ceiling: **7,500 UTF-8 bytes**.
- The canonical host-instructions file must satisfy both limits and is pasted into the Instructions field verbatim.
- The PrizePicks addendum is installed as a **Knowledge file**, not appended to the Instructions field.
- The deterministic editor-sync builder must fail if the canonical Instructions field exceeds either limit, if the canonical Action operation count is not exactly **20**, or if a required Action operation is missing.
- ChatGPT Custom GPT editor constraint observed 2026-09-24: **Action sets cannot have duplicate domains**. Therefore the production WOW Render domain may appear in only one Action group.
- The canonical live Action schema exposes all **20** WOW operations under `https://wow-governed-probability-engine.onrender.com`, including the three run-control operations:
  - `getWowV17PickRequestRunState`
  - `runWowV17ResumablePickRequest`
  - `closeWowV17PickRequestRun`
- The same single Action group now also exposes the research-only NCAAF spread forward-shadow operation:
  - `/internal/v17/spread-forward-shadow` -> `scoreWowV17SpreadForwardShadow`
- `scoreWowV17SpreadForwardShadow` accepts only the closed `NCAAF` request contract with event identity/time, home/away teams, exact home spread, and season. It is research-only and does not certify, promote, publish, or execute a spread probability.
- `artifacts/wow-engine/v17/openapi.wow-betting-engine.v17.run-control.yaml` remains a repository/reference contract only. It is **not** installed as a second Custom GPT Action because the editor rejects two Action sets for the same domain.
- Canonical prop operations include:
  - `/score-prop` -> `scoreWowProp`
  - `/score-pick-request` -> `scoreWowPickRequest`
- Authentication remains API Key -> Bearer using the existing `WOW_ACTION_API_KEY`.
- `can_execute=false`, dry-run-only behavior, and `V17_TERMINAL_REDUCER` authority remain binding.

## PR #766 host-contract behavior

The PrizePicks Knowledge contract requires the live host to:

- enumerate and inspect every page of an attached multi-page PDF before scoring/ranking;
- retry a blank, clipped, or illegible page through the available page-level render/screenshot path before declaring it unreadable;
- preserve `SOURCE_PAGE_UNREADABLE:<page_number>` and page-count reconciliation when source ingestion remains incomplete;
- never claim `omitted rows = 0` or full-board reconciliation while unknown rows can remain on unreadable pages;
- treat unreadable source pages as source-ingestion blockers, not `MODEL_UNAVAILABLE`; and
- render distinct `Player`, `Matchup`, `PrizePicks line`, `Offer`, `Available side(s)`, and `Current/live note` columns.

Repository merge/CI does not itself update the OpenAI Custom GPT editor. Current live editor parity therefore remains fail closed until the canonical instructions are saved, the PrizePicks addendum is attached as Knowledge, the single canonical 20-operation Action schema is imported with Bearer authentication, the editor is saved/reloaded, and acceptance succeeds from a fresh production WOW chat.

## Acceptance required to re-attest VERIFIED

The production WOW editor must be saved/reloaded with:

1. `WOW_V17_CUSTOM_GPT_INSTRUCTIONS.txt` in the Instructions field;
2. the PrizePicks addendum attached as Knowledge (`WOW_V17_PRIZEPICKS_HOST_CONTRACT_KNOWLEDGE.txt` from the sync artifact, or the byte-identical canonical addendum source);
3. exactly one WOW Action group for `wow-governed-probability-engine.onrender.com`, imported from `openapi.wow-betting-engine.v17.yaml`, exposing all 20 operations with existing Bearer authentication preserved.

Then a fresh production WOW chat must prove:

1. `getWowV17BackendHealth` is visible and `/health` reaches the production Render backend;
2. Bearer authentication succeeds without exposing or replacing `WOW_ACTION_API_KEY`;
3. `scoreWowPickRequest` is callable and returns a typed governed response/receipt;
4. `lookupWowV17PredictionReceipts` is callable and can recover the immutable receipt;
5. `getWowV17PickRequestRunState`, `runWowV17ResumablePickRequest`, and `closeWowV17PickRequestRun` are visible on the same WOW Action surface;
6. `scoreWowV17SpreadForwardShadow` is visible on that same Action surface and a valid NCAAF exact-line request reaches the backend without becoming publishable, promotable, or executable;
7. a multi-page PrizePicks attachment follows the page-completeness contract, including typed unreadable-page behavior if applicable;
8. required V17 diagnostics remain callable; and
9. `can_execute=false` remains true.

Until those checks are observed, report:

```text
LIVE_GPT_EDITOR_SYNC = RESYNC_REQUIRED_AFTER_ISSUE871_ACTION_SCHEMA_UPDATE__LIVE_ACTION_ACCEPTANCE_REQUIRED
USER_JOURNEY_HEALTH = FAIL
can_execute = false
```

Do not report `VERIFIED`, and do not downgrade editor/session synchronization failures into sporting-model failures such as `MODEL_UNAVAILABLE`, `MODEL_INPUTS_INSUFFICIENT`, `MODEL_SCORER_FAILED`, or `MODEL_OUTPUT_INVALID`.
