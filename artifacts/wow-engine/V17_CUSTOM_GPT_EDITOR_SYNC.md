# WOW V17 Custom GPT editor synchronization

Updated: 2026-09-21

Status: **EDITOR_UPDATED__LIVE_ACTION_ACCEPTANCE_REQUIRED**

Repository/backend repairs through PR #617 are merged, and the post-PR617 production `WOW_BETTING_ENGINE` editor update was user-confirmed on 2026-09-20. The editor was updated with the pinned canonical V17 schema, `scoreWowPickRequest` was visible, the production Render target remained configured, the existing Bearer credential was left untouched, the GPT update was saved, and a fresh chat was opened.

That resolves the stale repository statement that the editor had not yet been updated. It does **not** by itself establish `LIVE_GPT_EDITOR_SYNC=VERIFIED`: a post-update authenticated Action acceptance receipt from the fresh live GPT session is still required.

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

The production `WOW_BETTING_ENGINE` editor was positively saved/reloaded and Action-tested on 2026-09-16. That historical attestation proves the editor previously had a working bearer-authenticated Action connection.

Historical facts only:

- prior token: `LIVE_EDITOR_SYNC_VERIFIED`;
- prior live pick operation included `scoreWowPickRequest`;
- prior health invocation reached the production Render origin and returned V17 active with `can_execute=false`;
- no credential was exposed.

## Post-PR617 editor update evidence

User-confirmed on 2026-09-20:

1. the pinned canonical V17 Action schema was imported into the production WOW editor;
2. `scoreWowPickRequest` was present in the installed Action surface;
3. the Action server target remained `https://wow-governed-probability-engine.onrender.com`;
4. the existing Bearer authentication configuration was preserved and not replaced;
5. the GPT editor update was saved; and
6. a fresh WOW chat was opened so the newly registered Action schema could bind to the new session.

This is sufficient to retire `RESYNC_REQUIRED_AFTER_PR617` as the editor-state description. It is not sufficient to assert live Action acceptance.

## Remaining acceptance to re-attest VERIFIED

To move `LIVE_GPT_EDITOR_SYNC` to `VERIFIED`, the fresh production WOW chat must still provide post-update evidence that:

1. a live `/health` Action invocation reaches the production Render backend;
2. Bearer authentication succeeds without exposing or replacing `WOW_ACTION_API_KEY`;
3. `scoreWowPickRequest` is actually callable from that fresh live chat and returns a typed governed response/receipt;
4. required V17 diagnostic Actions remain callable; and
5. `can_execute=false` remains true.

Until those live Action checks are observed, report `LIVE_GPT_EDITOR_SYNC=EDITOR_UPDATED__LIVE_ACTION_ACCEPTANCE_REQUIRED`, not `VERIFIED` and not `RESYNC_REQUIRED_AFTER_PR617`.

## Backend route evidence

Production route evidence is independently healthy: `/score-pick-request` is mounted and bearer-protected, and the current `wow-governed-probability-engine` deployment is live. Backend route health is not a substitute for the live GPT Action acceptance above.

## Status separation

- `BACKEND_RUNTIME`: V17 active when backend confirms it.
- `MODEL_CAPABILITY`: route-specific; preserve exact typed status.
- `REPOSITORY_GOVERNANCE`: PR #617 merged; canonical Action IDs repaired.
- `LIVE_GPT_EDITOR_SYNC`: **EDITOR_UPDATED__LIVE_ACTION_ACCEPTANCE_REQUIRED**.
- `USER_JOURNEY_HEALTH`: remains independent and fail-closed until the golden ChatGPT -> Action -> governed-result canary completes.

A product/editor synchronization or acceptance problem must never be rewritten as `MODEL_UNAVAILABLE`, `MODEL_INPUTS_INSUFFICIENT`, `MODEL_SCORER_FAILED`, or `MODEL_OUTPUT_INVALID`.
