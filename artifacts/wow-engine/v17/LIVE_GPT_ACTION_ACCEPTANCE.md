# WOW V17 Live GPT Action Acceptance

Status: `REQUIRED_BEFORE_LIVE_GPT_EDITOR_SYNC_VERIFIED`

This acceptance is intentionally separate from repository state and backend health. A current repository schema and a healthy Render deployment do not prove that a newly opened production WOW GPT chat has the canonical Action operations bound to its tool surface.

## Required acceptance sequence

A live editor sync may be reported as `VERIFIED` only after all checks below complete from a fresh production WOW Betting Engine chat:

1. `getWowV17BackendHealth` is visible and callable.
2. The health Action reaches `https://wow-governed-probability-engine.onrender.com` and returns HTTP 200.
3. Bearer authentication succeeds using the configured `WOW_ACTION_API_KEY`; the credential value must never be exposed.
4. `scoreWowPickRequest` is visible and callable.
5. Submit one known pregame directional prop through `scoreWowPickRequest` with a stable `request_id` and `row_key`.
6. The scoring call returns a typed governed row result or immutable prediction receipt.
7. `lookupWowV17PredictionReceipts` is visible and callable.
8. Look up the exact scored row by `prediction_id` when available, otherwise by exact event/player/stat/line/direction identity, and confirm the immutable receipt is recoverable.
9. Required diagnostic Actions remain visible and callable.
10. Confirm `can_execute=false` remains invariant.

## Failure semantics

If the repository schema is current and the backend is healthy but the fresh production GPT chat does not expose/call the canonical Actions:

```text
LIVE_GPT_EDITOR_SYNC = EDITOR_UPDATED__LIVE_ACTION_ACCEPTANCE_REQUIRED
ACTION_STATUS = LIVE_GPT_ACTION_INVOCATION_BLOCKED
MODEL_CAPABILITY = UNCHANGED_ROUTE_SPECIFIC_STATE
can_execute = false
```

Do not rewrite this state as `MODEL_UNAVAILABLE`, `MODEL_SCORER_FAILED`, `FULL_MODEL_NOT_RUN`, or a model/calibration defect.

If an Action is visible and invoked but the selected scorer itself throws, times out, or returns no valid completion, preserve the backend scorer failure semantics instead.

## Large-board interactive scoring

For production GPT interactive runs, keep calls bounded to no more than 4 directional rows per `scoreWowPickRequest` call even though the OpenAPI transport schema permits a larger batch. Use one parent request identity, unique row keys, immutable receipt lookup before retry after ambiguous completion, and exact-once reconciliation. Do not rank a partial pool.

## Acceptance record

Record:

```text
acceptance_timestamp_utc
fresh_chat_id_or_attestation_reference
backend_health_status
auth_status
score_action_visible
score_action_result_status
receipt_lookup_visible
receipt_lookup_status
diagnostic_actions_status
can_execute
verdict
```

Only `verdict=PASS` permits `LIVE_GPT_EDITOR_SYNC=VERIFIED`.
