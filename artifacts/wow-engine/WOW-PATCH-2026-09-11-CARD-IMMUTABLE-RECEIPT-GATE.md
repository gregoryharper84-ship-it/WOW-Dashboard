# WOW-PATCH-2026-09-11-CARD-IMMUTABLE-RECEIPT-GATE

## Status

```text
status=L3_PATCH_CANDIDATE_IMPLEMENTED_ON_BRANCH
patch_priority=P0
runtime_generation=V17_ACTIVE
scope=CARD_CONSTRUCTION_AND_RECOMMENDATION_PERSISTENCE
terminal_authority=V17_TERMINAL_REDUCER
can_execute=false
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true
```

## Why This Patch Is Earned

The September 10, 2026 postmortem produced a specific construction failure pattern without sufficient evidence for a sporting-model recalibration:

```text
research-only / unmodeled / blocked row
-> admitted to a high-hit-probability card
-> became a critical card hinge
```

The settled batch contained 11 visible legs, 9 wins, and 2 losses, but the exact settled selections did not reconcile to exact immutable pregame governed prediction records. Adjacent lines existed for some MLB players, but exact-line governance prohibits inheriting qualification across materially different thresholds.

The repair is therefore a construction/persistence gate, not a probability haircut, calibration change, or lane suspension.

## Preserve Targets

This patch MUST preserve:

```text
successful sporting-model probabilities
existing calibrated probabilities and lower bounds
exact-line discipline
research/discovery visibility
typed upstream blockers
session exposure / duplicate-thesis governance
same-event correlation governance
mandatory shrink / no-filler behavior
V17_TERMINAL_REDUCER authority
can_execute=false
```

It MUST NOT lower a sporting probability merely because a card lost or because a thesis appears on multiple cards.

## Binding Card Admission Rule

Before any leg may enter a card presented as governed, Full Model, model-qualified, highest-hit-probability, or equivalent, the card layer must prove an exact immutable pregame governed prediction receipt for that exact leg.

The identity comparison is:

```text
official event / canonical event identity
+ participant/player/team/selection
+ market/stat family
+ period when applicable
+ exact line or threshold when applicable
+ direction/side when applicable
+ settlement basis
```

Hard invariant:

```text
materially_different_line_or_direction != same_prediction
```

Examples:

```text
Max Fried MORE 3.5 K cannot inherit a Max Fried MORE 4.5 K receipt.
Ryan Feltner LESS 4.5 K cannot inherit a Ryan Feltner 3.5 K receipt.
Ryan Feltner LESS 6.5 K cannot inherit a Ryan Feltner 3.5 K receipt.
A winning research-only row does not become a governed pregame prediction after settlement.
A blocked-but-winning row does not become card eligible after settlement.
```

## Receipt Requirements

A valid receipt must expose or resolve to:

```text
governed_prediction_id
governed_prediction_table
exact canonical identity fields
is_immutable_pregame=true
probability_publishable=true
lane_card_eligible=true
valid calibrated_probability
valid calibrated_lower_bound
no controlling terminal blocker
can_execute=false
```

`lane_card_eligible` is upstream-owned. The card layer may not manufacture it from:

```text
sportsbook implied probability
external projection
research confidence
recent hit rate
model capability availability
numeric probability alone
postgame result
```

When the controlling lane requires rank eligibility or another stricter publication condition, that condition remains part of upstream `lane_card_eligible`; this patch cannot weaken it.

## Missing Receipt / Mismatch Behavior

Fail closed with construction-layer blockers such as:

```text
CARD_NO_EXACT_GOVERNED_PREGAME_RECEIPT
CARD_GOVERNED_PREDICTION_ID_MISSING
CARD_GOVERNED_PREDICTION_TABLE_MISSING
CARD_IDENTITY_INCOMPLETE:<dimension>
CARD_EXACT_IDENTITY_MISMATCH:<dimension>
CARD_IMMUTABLE_PREGAME_RECEIPT_NOT_VERIFIED
CARD_SPORTING_PROBABILITY_NOT_PUBLISHABLE
CARD_UPSTREAM_ELIGIBILITY_NOT_VERIFIED
CARD_UPSTREAM_TERMINAL_BLOCKER
CARD_GOVERNED_PROBABILITY_PACKAGE_INVALID
CARD_CAN_EXECUTE_INVARIANT_UNVERIFIED
```

These are card/construction blockers. They do not replace or rename the upstream model's native typed result.

## Mandatory Shrink / No Filler

If a proposed final leg fails this gate:

```text
remove ineligible leg
-> do not backfill with research-only or weaker filler
-> preserve any already-qualified independent legs
-> shrink card
```

If shrink takes the card below its platform/governance minimum:

```text
portfolio_status=HELD
blocker=INSUFFICIENT_LEGS_AFTER_MANDATORY_SHRINK
```

Replacement search, when separately performed, must still satisfy the existing session-exposure, dependence/correlation, weakest-leg, freshness, and strictly-superior-replacement rules.

## Recommendation Persistence Contract

Every governed recommendation/card leg should persist enough linkage to make later postmortem reconciliation deterministic:

```text
governed_prediction_id
governed_prediction_table
official_event_id or canonical event id
participant/player/team
market/stat
period where applicable
exact_line where applicable
direction_or_side where applicable
settlement_basis
terminal_label
probability_publishable
lane_card_eligible / equivalent governed admission proof
model_timestamp / pregame capture proof
capture_timing
can_execute=false
```

Where the existing recommendation schema stores some identity dimensions inside a canonical payload rather than dedicated columns, the write path must preserve them losslessly. A postmortem must not reconstruct a missing exact line or direction from memory.

## Implementation

Repository module:

```text
card_immutable_receipt_gate.py
```

Exports:

```text
evaluate_card_leg_admission(...)
gate_proposed_card(...)
```

The module is intentionally construction-only. It does not score, calibrate, change probabilities, search for replacement candidates, or authorize execution.

## Regression Requirements

1. Exact governed receipt passes.
2. Adjacent-line receipt cannot authorize the target line.
3. Opposite direction/side cannot authorize the target leg.
4. Research-only winner with no exact receipt remains ineligible.
5. Blocked-but-winning row remains ineligible.
6. Invalid/missing calibrated probability package is rejected.
7. Missing explicit `can_execute=false` fails closed.
8. Ineligible leg forces shrink rather than filler.
9. Shrink below platform minimum produces HOLD + `INSUFFICIENT_LEGS_AFTER_MANDATORY_SHRINK`.
10. Card gate does not mutate probability fields.
11. Team/event winner rows without a scalar line can pass when all applicable identity dimensions match exactly.
12. Existing duplicate-thesis/session-exposure and same-event correlation regressions remain green.
13. V17 typed model failure semantics remain unchanged.
14. `can_execute=false` remains invariant.

## Not Authorized By This Patch

Do NOT implement from this postmortem alone:

```text
NFL probability weight changes
Rams ML calibration changes
MLB pitcher-K calibration changes
global lower-bound tightening
universal probability haircuts
broad sport/market suspension
```

Those require separate cohort/model evidence.

## Promotion Criteria

This patch may move beyond L3 only after:

```text
root cause confirmed
host/card-builder binding confirmed
P0 receipt-gate regressions pass
existing session-exposure regressions pass
full required CI passes
historical replay confirms exact-line mismatches are blocked
nearest successful comparable governed cards remain admitted
no unrelated sporting probability changes
can_execute=false regression passes
```

Repository implementation or PR merge alone does not establish `FIXED_VERIFIED`. Production deployment/version confirmation and replay are separate, and `LIVE_GPT_EDITOR_SYNC` remains a distinct state.

## One-Line Definition

**V17-CARD-IMMUTABLE-RECEIPT-GATE prevents research-only, blocked, unlogged, adjacent-line, or otherwise non-identical predictions from becoming governed card legs; invalid legs are removed and the card shrinks rather than using filler, while sporting probabilities remain unchanged.**
