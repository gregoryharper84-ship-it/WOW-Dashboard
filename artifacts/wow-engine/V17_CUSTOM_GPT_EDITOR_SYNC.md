# WOW V17 Custom GPT editor synchronization

Updated: 2026-09-18

Status: **LIVE_EDITOR_SYNC_REQUIRED_AFTER_P0_D**

The production `WOW_BETTING_ENGINE` editor was last saved, reloaded, and acceptance-tested on 2026-09-16. The historical state was `LIVE_EDITOR_SYNC_VERIFIED`; it is not the current state. Repository semantic instructions later changed for the P0-D large-prop-pool completion/receipt-recovery contract, so a new editor save/reload acceptance is required. Repository, backend/runtime, and live editor state remain separate.

## Last verified live state

- Historical 2026-09-16 token: `LIVE_EDITOR_SYNC_VERIFIED`.
- Current `LIVE_GPT_EDITOR_SYNC`: **RESYNC_REQUIRED_AFTER_P0_D**.
- The Sep 16 live instructions reported 5,418 characters, below the 8,000-character limit.
- The saved instructions preserved `can_execute=false`, dry-run behavior, WOW prop ownership, LLP team/event ownership, one controlling specialist per row/event, Scout/Research as evidence-only, governed calibrated probability/lower-bound rules, typed failures, immutable receipt identity, exact-line/OOD handling, and secret non-exposure.
- The live Action contained 14 operations and retained pick-request/team-event/record/settle plus V17 host/detailed-evidence/daily-row/receipt operations.
- Authentication remained API Key -> Bearer using `WOW_ACTION_API_KEY`; the credential was not exposed or re-entered during verification.
- Live acceptance invoked `getWowProbabilityHealth` against the production origin and returned V17 active, external governed backend, and `can_execute=false`.

## P0-D editor delta requiring synchronization

`WOW_V17_CUSTOM_GPT_INSTRUCTIONS.txt` now adds a large-board contract that is not proven live until the editor is saved/reloaded again:

- backend `/score-pick-request` keeps <=50-row schema capability, while LIVE_GPT interactive scoring uses <=3 directional rows per Action call;
- stable row/chunk identity is preserved across the pool;
- timeout/disconnect/ambiguous completion triggers exact immutable receipt lookup before retry;
- only unresolved rows are retried, splitting to one row if needed;
- successful earlier receipts are preserved; and
- a partial pool is never ranked or described as Full Model completion.

Until this text is saved and acceptance-tested in the live GPT editor, repository correctness must not be reported as live-host completion.

## Action source reconciliation

The live editor schema is a merged contract sourced from:
- `artifacts/wow-engine/openapi.custom-gpt.template.yaml`
- `artifacts/wow-engine/openapi.pick-request-action.yaml`
- verified active V17 host/detailed-evidence/daily-row/prediction-receipt operations

The claim that `v17/openapi.wow-betting-engine.v17.yaml` alone is the live installed schema is obsolete. The last verified live pick operation is `scoreWowPickRequest`, not `scoreWowV17PickRequest`; batch/ledger operations include `scoreWowTeamEventRequest`, `recordWowRecommendations`, and `settleWowRecommendations`.

## Repository text parity note

The prior instruction blob SHA must not be reported as the current live-editor instruction hash. `WOW_V17_CUSTOM_GPT_INSTRUCTIONS.txt` is the repository semantic source; after P0-D, semantic parity with the live editor is pending a fresh save/reload acceptance.

## OpenAPI introspection defect is separate

The deployed backend routes are live, while server-generated `/openapi.json` still has a known `market_api.ScorePropRequest` forward-reference defect. Direct production probes established the relevant scoring/ledger routes. Do not rewrite an editor synchronization requirement as a model failure.

## Status separation

- `BACKEND_RUNTIME`: V17 active when backend confirms it.
- `MODEL_CAPABILITY`: route-specific; preserve exact typed status.
- `REPOSITORY_GOVERNANCE`: protected-main/CI/repository state.
- `LIVE_GPT_EDITOR_SYNC`: **RESYNC_REQUIRED_AFTER_P0_D**; historical 2026-09-16 state was `LIVE_EDITOR_SYNC_VERIFIED`.

A product/editor problem must never be rewritten as `MODEL_UNAVAILABLE`, `MODEL_INPUTS_INSUFFICIENT`, `MODEL_SCORER_FAILED`, or `MODEL_OUTPUT_INVALID`.
