# WOW V17 NFL Pick'em Pool Optimizer

Status: CLASS_B_ACTIVE_WITH_FOLLOW_ON_REVIEW_PATCH
Original branch: `feature/nfl-pickem-pool-optimizer`
Original issue: #1073
Original pull request: #1077
Follow-on issue: #1334
Follow-on pull request: #1335
Follow-on branch: `patch/nfl-pickem-week4-learning-2026-10-04`

## Purpose

Add a pick'em decision layer for NFL pools that require one outright winner per game. This layer does **not** create, blend, alter, recalibrate, or replace sporting probabilities.

The controlling probability remains:

```text
sport=NFL
market_family=OUTRIGHT_WINNER
period=FULL_GAME / FULL_GAME_INCLUDING_OVERTIME
controlling_specialist=wow.nfl-game-win-probability-expert
terminal_authority=V17_TERMINAL_REDUCER
can_execute=false
```

## Decision Objective

`MAX_EXPECTED_CORRECT`

For every event with a completed and valid V17-governed NFL sporting-probability result, the pick'em layer preserves the controlling specialist's `selected_participant` as `pool_pick`.

Pick'em eligibility is deliberately separate from betting/publication eligibility. A source row may be held at a probability-bearing terminal such as `MODEL_QUALIFIED_HOLD` because downstream betting/publication governance is not proven and still answer the pool-only question, provided the sporting probability itself is complete, current, visible, internally consistent, and governed. The source terminal is preserved verbatim; the pick'em layer never upgrades it to `FINAL_APPROVED` and never makes it wager-eligible.

It must never use any of the following to change the pick:

- sportsbook moneyline or implied probability
- market consensus
- pool popularity
- recent results
- generic LLM judgment
- external projections

A later `POOL_WIN_EQUITY` strategy is explicitly unsupported until it has a separate governed design. The current implementation raises `PICKEM_STRATEGY_MODE_UNSUPPORTED` rather than silently adding contrarian behavior.

## Required Validation

Before a pick may be emitted:

1. Canonical NFL event identity must be complete and unique.
2. `sporting_probability_completed=true` and the sporting status must be completed.
3. Probability fields must not be withheld and model probability must not be marked unavailable.
4. The source terminal must be a recognized probability-bearing V17 state: `MODEL_QUALIFIED_HOLD`, `MARKET_VERIFIED_HOLD`, `MONEY_QUALIFIED`, or `FINAL_APPROVED`.
5. Typed model/scorer/input/output/staleness failures remain blocking regardless of numeric fields.
6. Global terminal authority must be `V17_TERMINAL_REDUCER`.
7. `can_execute=false` is mandatory.
8. Model version, model timestamp, calibration method/version, and source snapshot provenance must be present.
9. Home and away calibrated probabilities must normalize to 1.0.
10. `selected_participant` and `calibrated_selection_probability` must agree with the controlling two-sided NFL distribution and identify its higher-probability side.
11. The selected lower and upper calibration bounds must exist, agree across available aliases, and contain the selected calibrated probability.

Typed V17 failures are preserved verbatim. `MODEL_SCORER_FAILED`, `MODEL_INPUTS_INSUFFICIENT`, `MODEL_OUTPUT_INVALID`, `STALE_MODEL_OUTPUT`, `MODEL_UNAVAILABLE`, `MODEL_ROUTE_UNSUPPORTED`, and `RUN_INVALID_ACQUISITION_INCOMPLETE` must not be collapsed into another model status.

## Runtime Contract

The active runtime installs:

```text
POST /v17/nfl-pickem-board
operationId=runWowV17NFLPickemBoard
runtime_contract=V17_NFL_PICKEM_RUNTIME_V1
```

The route is authenticated with the existing V17 Action bearer dependency. It accepts multiple requested slate dates because an NFL pick'em week can span Thursday, Sunday, and Monday.

Discovery is canonical and schedule-first. The route reads the same immutable NFLVERSE schedule snapshot used by the NFL team/event specialist, filters requested regular-season dates, requires pregame rows, parses canonical kickoff time, and then invokes the existing registered NFL team/event scorer. Sportsbook or market discovery is not required to create the slate and never becomes sporting-probability evidence.

The existing V17 model invocation limit remains a bounded per-batch safety limit. The route does **not** increase that global limit and does **not** treat it as a terminal slate cap. All canonical events are continued across sequential bounded batches; within each batch the existing registered scorer is invoked concurrently.

For a 16-game Week 4 slate with the normal limit of 12, the expected continuation shape is:

```text
batch_1.events_attempted=12
batch_1.continuation_required=true
batch_2.events_attempted=4
batch_2.continuation_required=false
```

Full source scoring receipts are persisted through the existing V17 Daily row-detail ledger for auditability. Persistence failure is reported explicitly and never changes a sporting probability.

## Board Contract

The board builder returns exactly one pick per unique valid governed event row. Duplicate event rows are blocked with `PICKEM_DUPLICATE_GOVERNED_EVENT_ROW`.

For the supplied Week 4 sheet, the acceptance target is:

```text
requested_slate_dates=[2026-10-01, 2026-10-04, 2026-10-05]
expected_game_count=16
ready_pick_count=16
blocked_event_count=0
submission_ready=true
```

If any event lacks a valid completed governed sporting probability, the board becomes `PICKEM_BOARD_INCOMPLETE` or `PICKEM_BOARD_PARTIAL`; the system does not fabricate a selection from price, popularity, or generic reasoning.

## Confidence Labels

Confidence labels are downstream descriptive bands only. They never alter probability:

```text
STRONG   >= 0.70
MODERATE >= 0.60
LEAN     >= 0.55
TOSS_UP  <  0.55
```

The separate `selection_volatility_band` is derived only from the governed home/away probability gap.

## Week 4 Learning Overlay — Review, Not Probability Mutation

The 2026 Week 4 postmortem justified a downstream review-routing patch but **not** a fitted-model or calibration change from one slate of outcomes.

The pick'em output now classifies each valid governed pick into one review state:

```text
HIGH_CONFIDENCE_HOLD
STANDARD_HOLD
MODEL_SIDE_FRAGILITY_REVIEW
TOSS_UP_REVIEW
```

This overlay may consume only values already present in the governed sporting-probability package:

- selected calibrated probability;
- selected calibrated lower/upper bounds;
- two-sided calibrated probability gap;
- `model_disagreement` when the controlling scorer exposes it.

It never manufactures disagreement and never uses a sportsbook price, market consensus, external projection, recent result, or pool popularity to alter the pick.

Review-routing signals include a toss-up point probability, narrow two-sided gap, lower bound below 55%, wide calibration interval, or material governed model disagreement. These thresholds are **diagnostic routing thresholds only**. They are not fitted coefficients, calibration rules, publication qualification thresholds, or automatic upset triggers.

A strong model side with point probability at least 68%, lower bound at least 60%, a two-sided gap of at least 16 percentage points, and no material exposed model disagreement is tagged `HIGH_CONFIDENCE_HOLD` with:

```text
postmortem_learning_action=PRESERVE_UNLESS_COHORT_EVIDENCE
```

This explicitly prevents an isolated upset from teaching the system to downgrade otherwise strong pregame process. Fragile/toss-up cases are tagged for deeper review with `DEEP_REVIEW_NO_AUTOMATIC_FLIP`.

Hard invariants:

```text
review_overlay_can_change_pool_pick=false
review_overlay_can_change_probability=false
POOL_WIN_EQUITY remains unsupported
can_execute=false
```

Board output also reports counts for high-confidence holds, model-side fragility reviews, and toss-up reviews so Week 5 analysis can focus attention on the uncertain games without changing the governed NFL forecast.

## Monday Tiebreaker

The pick'em sheet requests total points for the Monday night game. Repository audit on 2026-09-30 found sportsbook total-market routing, but no registered certified NFL full-game total-points fitted specialist.

Therefore the current contract is:

```text
status=UNAVAILABLE
blocker=NFL_CERTIFIED_FULL_GAME_TOTAL_MODEL_NOT_REGISTERED
moneyline_probability_reuse_allowed=false
sportsbook_total_substitution_allowed=false
can_execute=false
```

A new fitted total-points model would be Class C and requires challenger development, historical replay, calibration evidence, holdout/forward validation, regression, and governed promotion before use.

## Regression Coverage

`artifacts/wow-engine/tests/test_v17_nfl_pickem_pool_optimizer.py` covers:

- 16 games in -> exactly 16 required picks out
- sportsbook, market, and pool-popularity non-influence
- exact 50/50 tie preserving the controlling scorer choice
- `MODEL_QUALIFIED_HOLD` remains a pool pick without terminal upgrade
- typed scorer-failure preservation
- malformed calibration-bound rejection
- withheld probability rejection
- hard reject-terminal rejection
- stale model-output rejection
- selected-participant / probability consistency
- duplicate-event blocking
- unsupported strategy rejection
- material model-disagreement fragility review without pick mutation
- strong-model-side preserve guardrail after an isolated-loss pattern
- toss-up deep-review routing while preserving the controlling scorer choice
- board-level review-count reconciliation
- tiebreaker fail-closed behavior

`artifacts/wow-engine/tests/test_v17_nfl_pickem_runtime.py` covers:

- canonical Oct. 1 / Oct. 4 / Oct. 5 Week 4 inventory
- all 16 events scored through the registered NFL scorer
- 12 + 4 bounded continuation
- modeled-hold pick preservation
- exact typed scorer-failure propagation
- source-row persistence receipts
- invalid request/date/timezone/strategy rejection
- one-time runtime route installation with stable operation ID

Production promotion remains Class B and requires green CI, adversarial review, merge, deployment verification, and live route acceptance. No fitted model mathematics, coefficients, distributions, calibration, qualification threshold, or sporting probability behavior are changed by this patch.
