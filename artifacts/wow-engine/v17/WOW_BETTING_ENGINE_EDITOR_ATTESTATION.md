# WOW Betting Engine — V17 Custom GPT editor attestation

Status: `LIVE_EDITOR_SYNC_VERIFIED`

Verified: 2026-09-16

This record attests the live `WOW_BETTING_ENGINE` Custom GPT configuration after save, reload, and a real Action health invocation. It records product configuration only; backend runtime, model capability, and repository governance remain separate states.

## Live editor identity and safety

```text
custom_gpt_name = WOW Betting Engine
custom_gpt_identity = WOW_BETTING_ENGINE
host_role = PLAYER_PROP_AND_SCALAR_INTELLIGENCE
nested_custom_gpt_required = false
can_execute = false
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS = true
```

WOW owns player/scalar props. Team/event winner/favorite/underdog/upset objectives route to `LLP_TEAM_BETTING_ENGINE`. Scout/Research remain evidence-only. Exactly one controlling fitted specialist owns each row/event.

## Instructions verification

- Live editor instructions were rewritten in plain operational language and saved successfully.
- The editor reported 5,418 characters, below the 8,000-character product limit.
- Required behavior preserved: no wager execution, secrets non-exposure, governed-probability-only sourcing, calibrated lower-bound ranking where required, typed failure semantics, immutable pregame identity, receipt-gated display, exact-line/OOD behavior, and status separation.
- The previous instruction blob SHA `202157522b96921d973e7a9dbc1d373f95249eb7` is historical and must not be reported as the current live-editor hash.
- The exact byte-for-byte 5,418-character live text/hash was not available to this repository-write session. Repository semantic instructions are maintained in `WOW_V17_CUSTOM_GPT_INSTRUCTIONS.txt`; no false current hash is asserted here.

## Live Action contract

```text
server = https://wow-governed-probability-engine.onrender.com
auth = API Key / Bearer using existing WOW_ACTION_API_KEY
action_operation_count = 14
can_execute = false
```

The installed live schema is a merged contract sourced from:

```text
artifacts/wow-engine/openapi.custom-gpt.template.yaml
artifacts/wow-engine/openapi.pick-request-action.yaml
verified active V17 host/detailed-evidence/daily-row/prediction-receipt operations
```

Confirmed batch/ledger operation IDs include:

```text
scoreWowPickRequest
scoreWowTeamEventRequest
recordWowRecommendations
settleWowRecommendations
```

The previous statement that `v17/openapi.wow-betting-engine.v17.yaml` alone was the installed live Action schema is obsolete.

## Save/reload evidence

```text
save_result = GPT Updated
last_edited = Sep 16
updates_pending = false
verified_after_reload = true
live = true
bearer_auth_changed = false
credential_exposed = false
```

## Live Action acceptance

`getWowProbabilityHealth` was invoked from the GPT editor against the production `/health` Action and returned a healthy V17 response including:

```text
status = ok
Runtime = V17_ACTIVE
Host = EXTERNAL_GOVERNED_BACKEND
can_execute = false
```

This proves a real Action invocation against the live Render backend rather than a prose fallback.

## Backend OpenAPI note

The production routes required by the live Action are mounted, including `/score-pick-request`, `/score-team-event-request`, `/record-recommendations`, and `/settle-recommendations`. The server-generated `/openapi.json` remains incomplete because full FastAPI OpenAPI generation has a known unresolved `market_api.ScorePropRequest` forward-reference defect. Track that as a backend introspection bug; it does not reopen editor synchronization.

## Final attestation

```text
WOW_CUSTOM_GPT_EDITOR_SYNC = LIVE_EDITOR_SYNC_VERIFIED
BACKEND_RUNTIME = separate state
MODEL_CAPABILITY = route-specific separate state
REPOSITORY_GOVERNANCE = separate state
can_execute = false
```
