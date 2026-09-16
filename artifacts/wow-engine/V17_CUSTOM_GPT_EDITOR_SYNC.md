# WOW V17 Custom GPT editor synchronization

Updated: 2026-09-16

Status: **LIVE_EDITOR_SYNC_VERIFIED**

The production `WOW_BETTING_ENGINE` editor was updated, saved, reloaded, and acceptance-tested on 2026-09-16. Repository/backend/runtime state remains separate from editor state; this document records the live product-configuration result only.

## Verified live state

- `LIVE_GPT_EDITOR_SYNC`: **VERIFIED**.
- Instructions were rewritten in plain operational language and saved successfully; the live editor reported 5,418 characters, below the 8,000-character limit.
- The saved instructions preserve `can_execute=false`, dry-run only behavior, WOW prop ownership, LLP team/event ownership, one controlling specialist per row/event, Scout/Research as evidence-only, governed calibrated probability/lower-bound rules, typed failure semantics, immutable receipt identity, exact-line/OOD handling, and secret non-exposure.
- The live Action contains 14 operations. It preserves the base health/governance/score/settle surface, pick-request/team-event/record/settle operations, and the verified V17 host/detailed-evidence/daily-row/receipt operations required by the active workflow.
- Authentication remains API Key -> Bearer using the existing `WOW_ACTION_API_KEY`; the credential was not exposed or re-entered during verification.
- The GPT was saved successfully; `GPT Updated` was observed, `Last edited` became Sep 16, and `Updates pending` cleared.
- Live acceptance invoked `getWowProbabilityHealth` against `https://wow-governed-probability-engine.onrender.com/health` and returned `status: ok`, `Runtime: V17_ACTIVE`, `Host: EXTERNAL_GOVERNED_BACKEND`, and `can_execute=false`.

## Action source reconciliation

The live editor schema is a merged contract. Its repository source inputs are:

- `artifacts/wow-engine/openapi.custom-gpt.template.yaml`
- `artifacts/wow-engine/openapi.pick-request-action.yaml`
- the verified active V17 host/detailed-evidence/daily-row/prediction-receipt operations

The previous claim that `v17/openapi.wow-betting-engine.v17.yaml` alone was the live installed schema is obsolete.

The live pick-request operation is `scoreWowPickRequest` (not `scoreWowV17PickRequest`). The live batch/ledger operations include `scoreWowTeamEventRequest`, `recordWowRecommendations`, and `settleWowRecommendations`.

## Repository text parity note

The prior instruction blob SHA `202157522b96921d973e7a9dbc1d373f95249eb7` must not be reported as the current live-editor instruction hash. The exact 5,418-character live-editor text/hash was verified in the editor but is not available to this repository-write session as a byte-for-byte export. `WOW_V17_CUSTOM_GPT_INSTRUCTIONS.txt` is therefore maintained as the repository semantic source, while this status file records the independently verified live save/reload state. This is a repository-parity detail, not a pending live-editor synchronization.

## OpenAPI introspection defect is separate

The deployed backend routes are live, but the server-generated `/openapi.json` remains incomplete because full FastAPI OpenAPI generation has a known unresolved `market_api.ScorePropRequest` forward-reference defect. Direct production probes established that `/score-pick-request`, `/score-team-event-request`, `/record-recommendations`, and `/settle-recommendations` are mounted; production logs also recorded successful `POST /score-pick-request` responses.

Do not downgrade `LIVE_GPT_EDITOR_SYNC` because of this introspection defect. Track and repair OpenAPI generation separately.

## Status separation

- `BACKEND_RUNTIME`: V17 active when confirmed by backend/runtime evidence.
- `MODEL_CAPABILITY`: route-specific; preserve exact backend typed status.
- `REPOSITORY_GOVERNANCE`: protected-main/CI/repository state.
- `LIVE_GPT_EDITOR_SYNC`: **VERIFIED 2026-09-16**.

A product/editor problem must never be rewritten as `MODEL_UNAVAILABLE`, `MODEL_INPUTS_INSUFFICIENT`, `MODEL_SCORER_FAILED`, or `MODEL_OUTPUT_INVALID`.
