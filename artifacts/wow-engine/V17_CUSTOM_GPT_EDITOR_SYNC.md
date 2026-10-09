# WOW V17 Custom GPT editor synchronization

Updated: 2026-10-01

Status: **RESYNC_REQUIRED_AFTER_PR766__LIVE_ACTION_ACCEPTANCE_REQUIRED**

The production `WOW_BETTING_ENGINE` editor was historically saved/reloaded and Action-tested on 2026-09-16, and a later editor/schema update was user-confirmed on 2026-09-20. Those facts remain historical evidence only; later repository host-contract and Action-contract changes require a fresh live-editor resync and acceptance. The stable machine-consumed resync status token is intentionally retained while the Action surface continues to evolve.

## Current repository contract

- Canonical live Action schema: `artifacts/wow-engine/v17/openapi.wow-betting-engine.v17.yaml`.
- Canonical host instructions: `artifacts/wow-engine/WOW_V17_CUSTOM_GPT_INSTRUCTIONS.txt`.
- PrizePicks live-host addendum: `artifacts/wow-engine/WOW_V17_CUSTOM_GPT_PRIZEPICKS_SKILL_ADDENDUM.txt`.
- Pick Em live-host addendum: `artifacts/wow-engine/WOW_V17_CUSTOM_GPT_PICK_EM_SKILL_ADDENDUM.txt`.
- Custom GPT Instructions field hard limit: **8,000 characters**; repository safety ceiling: **7,500 UTF-8 bytes**.
- The canonical host-instructions file must satisfy both limits and is pasted into the Instructions field verbatim.
- The PrizePicks and Pick Em addenda are installed as separate **Knowledge files**, not appended to the Instructions field.
- The deterministic editor-sync builder must fail if the canonical Instructions field exceeds either limit, if the canonical Action operation count is not exactly **26**, or if a required Action operation is missing.
- ChatGPT Custom GPT editor constraint observed 2026-09-24: **Action sets cannot have duplicate domains**. Therefore the production WOW Render domain may appear in only one Action group.
- The canonical live Action schema exposes all **26** WOW operations under `https://wow-governed-probability-engine.onrender.com`, including the three run-control operations:
  - `getWowV17PickRequestRunState`
  - `runWowV17ResumablePickRequest`
  - `closeWowV17PickRequestRun`
- The same single Action group exposes durable Daily full-board transport:
  - `/v17/daily-snapshot-submit` -> `submitWowV17DailySnapshot`
  - `/v17/daily-snapshot-run/{run_id}` -> `getWowV17DailySnapshotRun`
- Full current-board/all-sport scans use durable Daily submit/poll; synchronous Daily remains bounded diagnostic transport.
- The same single Action group exposes the research-only NCAAF spread forward-shadow operation:
  - `/internal/v17/spread-forward-shadow` -> `scoreWowV17SpreadForwardShadow`
- The same single Action group exposes durable governed NFL Pick'em transport:
  - `/v17/nfl-pickem-submit` -> `submitWowV17NFLPickemBoard`
  - `/v17/nfl-pickem-run/{run_id}` -> `getWowV17NFLPickemRun`
- The long-running synchronous `/v17/nfl-pickem-board` backend route is intentionally **not exposed to the live Custom GPT Action surface**. Full-sheet Pick'em work must use submit/poll so Action transport never waits for the complete scoring run. Expected game count comes from the supplied weekly sheet; bye weeks may contain fewer than 16 games.
- Durable Pick'em delegates to the existing governed NFL outright-win specialist, preserves source terminals, never substitutes sportsbook/pool popularity for sporting probability, and keeps `can_execute=false`.
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

## Pick Em live-host behavior

The Pick Em Knowledge contract requires the live host to:

- route an attached NFL Pick'em form or `Run Pick Em Skill` request to the durable governed Pick'em workflow;
- derive expected game count from the supplied sheet and reconcile every row to canonical NFL identity;
- use `submitWowV17NFLPickemBoard` -> `getWowV17NFLPickemRun`, never the obsolete synchronous full-board Action;
- require `PICKEM_BOARD_READY`, `full_sheet_submission_ready=true`, exact game-count reconciliation, and zero blockers before declaring a form submission-ready;
- use the NFL fitted game-win specialist for sporting probabilities and the dedicated NFL total specialist for the tiebreaker;
- preserve current production strategy authority; shadow pool-equity logic cannot silently mutate a production pick;
- preserve typed transport/scorer/input/model failures;
- when a form image is supplied, preserve the original form format and annotate only green selected teams/checkboxes, Name (default `GH`), and the governed tiebreaker while leaving Total Correct blank; and
- verify the completed image 1:1 against the terminal card.

Repository merge/CI does not itself update the OpenAI Custom GPT editor. Current live editor parity therefore remains fail closed until the canonical instructions are saved, both the PrizePicks and Pick Em addenda are attached as Knowledge, the single canonical 26-operation Action schema is imported with Bearer authentication, the editor is saved/reloaded, and acceptance succeeds from a fresh production WOW chat.

## Acceptance required to re-attest VERIFIED

The production WOW editor must be saved/reloaded with:

1. `WOW_V17_CUSTOM_GPT_INSTRUCTIONS.txt` in the Instructions field;
2. the PrizePicks addendum attached as Knowledge (`WOW_V17_PRIZEPICKS_HOST_CONTRACT_KNOWLEDGE.txt` from the sync artifact, or the byte-identical canonical addendum source);
3. the Pick Em addendum attached as Knowledge (`WOW_V17_PICK_EM_HOST_CONTRACT_KNOWLEDGE.txt` from the sync artifact, or the byte-identical canonical addendum source);
4. exactly one WOW Action group for `wow-governed-probability-engine.onrender.com`, imported from `openapi.wow-betting-engine.v17.yaml`, exposing all 26 operations with existing Bearer authentication preserved.

Then a fresh production WOW chat must prove:

1. `getWowV17BackendHealth` is visible and `/health` reaches the production Render backend;
2. Bearer authentication succeeds without exposing or replacing `WOW_ACTION_API_KEY`;
3. `scoreWowPickRequest` is callable and returns a typed governed response/receipt;
4. `lookupWowV17PredictionReceipts` is callable and can recover the immutable receipt;
5. `submitWowV17DailySnapshot` and `getWowV17DailySnapshotRun` are visible; a full-board canary creates a durable run_id and polls it to terminal;
6. `getWowV17PickRequestRunState`, `runWowV17ResumablePickRequest`, and `closeWowV17PickRequestRun` are visible on the same WOW Action surface;
7. `scoreWowV17SpreadForwardShadow` is visible on that same Action surface and a valid NCAAF exact-line request reaches the backend without becoming publishable, promotable, or executable;
8. `submitWowV17NFLPickemBoard` and `getWowV17NFLPickemRun` are visible on that same Action surface; the Week 4 multi-date request creates a durable run_id, polls to terminal, preserves all 16 source rows and does not change NFL probability ownership or terminal semantics;
9. the obsolete synchronous `runWowV17NFLPickemBoard` Action operation is absent from the live surface;
10. a multi-page PrizePicks attachment follows the page-completeness contract, including typed unreadable-page behavior if applicable;
11. required V17 diagnostics remain callable; and
12. `Run Pick Em Skill` retrieves the Pick Em Knowledge contract in a fresh production chat and a supplied weekly sheet follows the complete-form/reconciliation rules without inventing rows or probabilities;
13. when a Pick'em form image is supplied, the completed image preserves the original format, highlights/checks exactly one governed selection per game, writes the requested/default `GH` name and governed tiebreaker, leaves Total Correct blank, and reconciles 1:1 to the terminal card; and
14. `can_execute=false` remains true.

Until those checks are observed, report:

```text
LIVE_GPT_EDITOR_SYNC = RESYNC_REQUIRED_AFTER_PR766__LIVE_ACTION_ACCEPTANCE_REQUIRED
USER_JOURNEY_HEALTH = FAIL
can_execute = false
```

Do not report `VERIFIED`, and do not downgrade editor/session synchronization failures into sporting-model failures such as `MODEL_UNAVAILABLE`, `MODEL_INPUTS_INSUFFICIENT`, `MODEL_SCORER_FAILED`, or `MODEL_OUTPUT_INVALID`.
