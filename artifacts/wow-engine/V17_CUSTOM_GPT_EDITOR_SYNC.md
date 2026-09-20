# WOW V17 Custom GPT editor synchronization

Updated: 2026-09-20

Status: **LIVE_EDITOR_SYNC_REQUIRED_AFTER_PR617**

Repository and backend contracts are repaired through PR #617, but the production `WOW_BETTING_ENGINE` editor has not yet been re-saved/reloaded against that repaired contract in this repository session. Repository correctness, backend runtime, model capability, and live editor state remain separate.

## Current repository contract

- Canonical Action schema: `artifacts/wow-engine/v17/openapi.wow-betting-engine.v17.yaml`.
- Canonical prop operations exposed by that schema:
  - `/score-prop` -> `scoreWowProp`
  - `/score-pick-request` -> `scoreWowPickRequest`
- Legacy `scoreWowV17Prop` / `scoreWowV17PickRequest` identifiers remain compatibility aliases only where backend host routing explicitly accepts them.
- Authentication remains API Key -> Bearer using `WOW_ACTION_API_KEY`.
- `can_execute=false`, dry-run-only behavior, and `V17_TERMINAL_REDUCER` authority remain binding.

## Large-board interactive contract

`WOW_V17_CUSTOM_GPT_INSTRUCTIONS.txt` is the semantic source for the live host. Current required behavior is:

- backend `/score-pick-request` keeps <=50-row API capability;
- LIVE_GPT interactive scoring uses <=4 directional rows per Action call;
- stable `row_key` / chunk identity is preserved;
- timeout/disconnect/ambiguous completion triggers immutable prediction-receipt lookup before retry;
- only unresolved rows are retried, splitting to one row when needed;
- successful earlier receipts are preserved; and
- a partial pool is never ranked or described as Full Model completion.

## Historical live editor evidence

The production `WOW_BETTING_ENGINE` editor was last positively saved/reloaded and Action-tested on 2026-09-16. That historical attestation proves the editor previously had a working bearer-authenticated Action connection, but it does not prove current semantic/schema parity after subsequent P0-D and PR #617 changes.

Historical facts only:

- prior token: `LIVE_EDITOR_SYNC_VERIFIED`;
- prior live pick operation included `scoreWowPickRequest`;
- prior health invocation reached the production Render origin and returned V17 active with `can_execute=false`;
- no credential was exposed.

## Required live product synchronization

To move `LIVE_GPT_EDITOR_SYNC` back to VERIFIED, the live GPT editor must be saved/reloaded using the current repository semantic instructions and canonical V17 Action schema, while retaining the existing Bearer credential. Acceptance must then prove at minimum:

1. `scoreWowPickRequest` is present and callable;
2. `/health` reaches the production Render origin;
3. bearer authentication succeeds without exposing or replacing `WOW_ACTION_API_KEY`;
4. current diagnostic operations for capabilities, TheRundown, Odds API, and compact ESPN discovery remain callable as required by the live-host contract; and
5. `can_execute=false` remains true.

Until that product/editor acceptance is completed, do not report `LIVE_GPT_EDITOR_SYNC=VERIFIED`.

## Backend route evidence

Direct production probing has established that `/score-pick-request` is mounted and bearer-protected: an unauthenticated request returns HTTP 401 for a missing/malformed Authorization header. That is evidence of a live protected route, not a model failure.

## Status separation

- `BACKEND_RUNTIME`: V17 active when backend confirms it.
- `MODEL_CAPABILITY`: route-specific; preserve exact typed status.
- `REPOSITORY_GOVERNANCE`: PR #617 merged; canonical Action IDs repaired.
- `LIVE_GPT_EDITOR_SYNC`: **RESYNC_REQUIRED_AFTER_PR617** until editor save/reload acceptance succeeds.

A product/editor synchronization problem must never be rewritten as `MODEL_UNAVAILABLE`, `MODEL_INPUTS_INSUFFICIENT`, `MODEL_SCORER_FAILED`, or `MODEL_OUTPUT_INVALID`.
