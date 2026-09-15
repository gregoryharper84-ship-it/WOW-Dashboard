# WOW-PATCH-2026-09-13-VISIBLE-TRACE-RECEIPTS

status=L3_PATCH_CANDIDATE_IMPLEMENTED_ON_BRANCH
patch_priority=P0
runtime_generation=V17_ACTIVE
terminal_authority=V17_TERMINAL_REDUCER
can_execute=false

## Problem
A governed probability can exist while the user-facing response omits the durable identifiers required to prove what was actually displayed. That weakens later postmortem attribution even when the recommendation ledger successfully recorded the selection.

## Narrow repair
Keep the existing immutable prediction and write-before-display recommendation ledgers. Add a backend-owned, user-visible publication trace receipt to `POST /record-recommendations`.

For every successfully recorded row return:
- `publication_trace_id`
- `recommendation_record_id`
- `governed_prediction_id` when the row is governed/publishable
- `governed_prediction_table` when available
- `terminal_label`
- `probability_publishable`
- `trace_status`
- `display_authorized=true`
- `recorded_at`
- `can_execute=false`

Persist the same trace metadata inside the immutable recommendation row's `display_payload._wow_trace` so a later audit can reconstruct the publication grouping without changing sporting probability or calibration fields.

## Card/slip rule
The host calls `recordWowV17Recommendations` once per final publication/card. The returned `publication_trace_id` is the user-facing `card_trace_id` for a card/slip. Child rows remain individually addressable through their `recommendation_record_id` and `governed_prediction_id`.

## Publication gate
- No final governed pick/card is displayed unless `display_authorized=true`.
- Every displayed row requires a `recommendation_record_id`.
- Any row represented as governed/model-qualified additionally requires its exact `governed_prediction_id`.
- A research-only/held row may still be written for audit, but its trace receipt must not be relabeled as governed.
- If recommendation registration fails or is unproven, preserve any completed sporting probability and block publication/traceability only. Do not rewrite the condition as `MODEL_UNAVAILABLE` or another model failure.

## Postmortem join contract
- `recommendation_record_id` = exact displayed pregame recommendation identity.
- `governed_prediction_id` = immutable underlying model forecast identity.
- `publication_trace_id` / `card_trace_id` = grouping identity for the displayed pick set/card.

## Security / anti-spoofing
`display_payload._wow_trace` is backend-owned. Caller-supplied values at that key are overwritten by the server before persistence.

## Preserve targets
This patch must not change:
- raw/model/calibrated probability
- calibrated lower/upper bounds
- model qualification thresholds
- controlling-specialist ownership
- card/exposure/correlation math
- typed model failure semantics
- V17 terminal authority
- `can_execute=false`

## Acceptance tests
1. Successful write returns a UUID `publication_trace_id` and one trace receipt per persisted row.
2. Each trace receipt exposes the exact `recommendation_record_id` returned by the ledger.
3. Governed/publishable rows expose the exact `governed_prediction_id` and `governed_prediction_table`.
4. Research-only rows remain visibly non-governed while still being traceable.
5. Backend-owned trace metadata is persisted in `display_payload._wow_trace`.
6. Caller attempts to spoof `_wow_trace` are overwritten.
7. Registration failure/unproven write returns `display_authorized=false` and does not authorize final publication.
8. `can_execute=false` remains invariant.
9. Existing settlement joins by `recommendation_record_id` remain unchanged.
10. No sporting probability/calibration field is mutated by trace publication.

## Promotion
Do not mark FIXED_VERIFIED until protected CI passes, the branch is merged/deployed, runtime response replay proves trace receipts are emitted, and LIVE_GPT_EDITOR_SYNC is saved/verified for the host instructions.