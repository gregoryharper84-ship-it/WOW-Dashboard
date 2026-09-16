# WOW V17 production status

Updated: 2026-09-16

This is the current-status pointer for the governed WOW V17 system. Historical proposal, migration, review, and incident documents remain archival and must not override this file when they describe an older lifecycle state.

## Current state

- Runtime: **V17 ACTIVE / VERIFIED_ACTIVE**
- Production service: `wow-governed-probability-engine`
- Render service ID: `srv-da7sa9gu01pc73brt80g`
- Production branch: `main`
- Production entrypoint: `api_ncaaf_acceptance:app`
- Verified deployed runtime SHA on 2026-09-16: `1762c4fc2c1a9f9670c3c5a6a0fe551ddb98f3cf`
- Render deploy: `dep-daldrgf40ujc73dm0a9g` reached `live`
- Render deployment policy: `autoDeploy=yes`, `autoDeployTrigger=checksPass`
- Global terminal reducer: `V17_TERMINAL_REDUCER`
- `WOW_CAN_EXECUTE=false`
- `WOW_DRY_RUN_ONLY=true`
- No wager/order execution path is authorized.

The earlier statement that this production service required manual deploy because auto-deploy was disabled is obsolete.

## Routing and probability contract

- WOW_BETTING_ENGINE owns player/prop/scalar intelligence.
- LLP_TEAM_BETTING_ENGINE owns team/event winner/favorite/underdog/upset intelligence.
- Scout and Research gather/reconcile evidence; they are not fitted probability publishers.
- Exactly one controlling specialist owns each row/event.
- Governed probability requires the correct fitted specialist and its valid probability/calibration/bound package.
- `MODEL_UNAVAILABLE` is reserved for true controlling-model capability absence. Input, scorer, output, host-Action, market, and publication failures retain their own typed semantics.
- Missing market evidence must not erase a completed sporting probability when the backend contract preserves it.
- `can_execute=false` remains invariant.

## Production route verification

The current production entrypoint mounts the V17/compatibility routes used by the Custom GPT. On 2026-09-16 production verification established:

- `/score-pick-request` is live; Render logs contain successful `POST /score-pick-request` 200 responses.
- `/score-team-event-request`, `/record-recommendations`, and `/settle-recommendations` are mounted; direct GET probes return 405 Method Not Allowed, proving the POST route exists.
- V17 startup logs report `WOW_V17_RUNTIME status=ACTIVE` and `can_execute=false`.

The server-generated `/openapi.json` remains incomplete because full FastAPI OpenAPI generation has a known unresolved `market_api.ScorePropRequest` forward-reference defect. This is an introspection defect, not evidence that the mounted routes are absent, and it is not an editor-sync blocker.

## Release acceptance

Production code changes require protected-branch CI and Render deployment through the configured `checksPass` policy. Required checks remain:

- `WOW governed probability backend`
- `WOW required-three regression`
- `WOW additional required regression`

A release is production-verified only after the intended commit reaches Render `live`, startup/runtime evidence is healthy, required migrations are present, and any scenario-specific acceptance is completed. Documentation-only commits need no manual redeploy; current Render policy handles eligible `main` changes automatically after checks pass.

## Repository governance

**VERIFIED.** `main` remains protected. Repository-governance state is separate from backend runtime, model capability, and live editor configuration and must never be reported as a sporting-model failure.

## Custom GPT editor synchronization

**LIVE_EDITOR_SYNC_VERIFIED — 2026-09-16.**

The live `WOW_BETTING_ENGINE` was edited, saved, reloaded, and health-tested against the production Render backend.

- Live instructions: plain operational rewrite; editor reported 5,418 characters, below the 8,000-character limit.
- Live Action: merged 14-operation contract using the base runbook contract, pick-request/ledger contract, and retained active V17 operations.
- Authentication: API Key/Bearer using the existing `WOW_ACTION_API_KEY`; credential not exposed.
- Save result: `GPT Updated`; `Last edited` Sep 16; no updates pending.
- Acceptance: `getWowProbabilityHealth` invoked the production `/health` Action and returned `status: ok`, `Runtime: V17_ACTIVE`, `Host: EXTERNAL_GOVERNED_BACKEND`, `can_execute=false`.

The old instruction blob SHA `202157522b96921d973e7a9dbc1d373f95249eb7` and the statement that the live Action schema remained unchanged are obsolete and must not be used as current attestations.

The exact byte-for-byte 5,418-character live instruction export/hash was not available to the repository-write session. That repository parity detail does not reopen the completed live editor synchronization.

## Status language

Report these independently:

- `BACKEND_RUNTIME = V17 ACTIVE`
- `MODEL_CAPABILITY = route-specific backend result`
- `REPOSITORY_GOVERNANCE = protected/CI state`
- `LIVE_GPT_EDITOR_SYNC = VERIFIED 2026-09-16`
