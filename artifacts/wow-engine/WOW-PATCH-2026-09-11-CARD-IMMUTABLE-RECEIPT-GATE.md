# WOW-PATCH-2026-09-11-CARD-IMMUTABLE-RECEIPT-GATE

status=L3_PATCH_CANDIDATE_IMPLEMENTED_ON_BRANCH
patch_priority=P0
runtime_generation=V17_ACTIVE
terminal_authority=V17_TERMINAL_REDUCER
can_execute=false

## Earned repair
The Sept. 10 postmortem exposed a construction failure: research-only, blocked, unlogged, or adjacent-line rows could enter a card presented as governed/high-hit-probability despite lacking an exact immutable pregame governed receipt. This patch repairs card admission only; it does not alter sporting-model probability, calibration, or thresholds.

## Binding rule
Before any leg enters a governed/Full Model/model-qualified/highest-hit-probability card, require an exact immutable pregame governed prediction receipt matching canonical event, participant/team, market/stat, period when applicable, exact line when applicable, direction/side when applicable, and settlement basis.

Required receipt evidence:
- governed_prediction_id + governed_prediction_table
- is_immutable_pregame=true
- probability_publishable=true
- lane_card_eligible=true
- valid calibrated_probability and calibrated_lower_bound
- no controlling terminal blocker
- can_execute=false

A different line or direction is not the same prediction. Sportsbook probability, projections, hit rate, research confidence, capability availability, or a postgame win cannot manufacture card eligibility.

## Fail-closed behavior
Ineligible legs are removed. Replacement remains subject to existing strictly-superior independent replacement rules; otherwise shrink the card. Never use filler. If shrink drops below minimum, HOLD with `INSUFFICIENT_LEGS_AFTER_MANDATORY_SHRINK`.

## Preserve targets
Sporting probabilities, calibrated probabilities/lower bounds, exact-line identity discipline, typed upstream failures, duplicate-thesis/session-exposure governance, V17_TERMINAL_REDUCER authority, and can_execute=false remain unchanged.

## Promotion
Repository code alone is not FIXED_VERIFIED. Require protected CI, merge/deploy/version confirmation, governed scenario replay, and separate LIVE_GPT_EDITOR_SYNC status where applicable.
