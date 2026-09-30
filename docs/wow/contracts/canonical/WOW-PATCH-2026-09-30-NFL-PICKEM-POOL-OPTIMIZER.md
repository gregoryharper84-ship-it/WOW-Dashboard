# WOW V17 NFL Pick'em Pool Optimizer

Status: CLASS_B_REVIEW_REQUIRED
Branch: `feature/nfl-pickem-pool-optimizer`
Issue: #1073

## Purpose

Add a pick'em decision layer for NFL pools that require one outright winner per game. This layer does **not** create, blend, alter, or replace sporting probabilities.

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

For every event with a completed, publishable, V17-governed NFL probability package, the pick'em layer preserves the controlling specialist's `selected_participant` as `pool_pick`.

It must never use any of the following to change that pick:

- sportsbook moneyline or implied probability
- market consensus
- pool popularity
- recent results
- generic LLM judgment
- external projections

A later `POOL_WIN_EQUITY` strategy is explicitly unsupported until it has a separate governed design. The current implementation raises `PICKEM_STRATEGY_MODE_UNSUPPORTED` rather than silently adding contrarian behavior.

## Required Validation

Before a pick may be emitted:

1. NFL event identity must be complete.
2. The canonical governed scoring package must pass `validate_governed_scoring_package`.
3. `sporting_probability_completed=true`.
4. Probability fields must not be withheld.
5. The source row must be `probability_publishable=true`, `rank_eligible=true`, and `terminal_label=FINAL_APPROVED`.
6. Global terminal authority must be `V17_TERMINAL_REDUCER`.
7. `can_execute=false`.
8. Home and away probabilities must normalize to 1.0.
9. `selected_participant` and `calibrated_selection_probability` must agree with the controlling two-sided NFL distribution.

Typed V17 model failures are preserved verbatim. `MODEL_SCORER_FAILED`, `MODEL_INPUTS_INSUFFICIENT`, `MODEL_OUTPUT_INVALID`, `STALE_MODEL_OUTPUT`, and `MODEL_UNAVAILABLE` must not be collapsed into another model status.

## Board Contract

The board builder returns exactly one pick per unique governed event row. Duplicate event rows are blocked with `PICKEM_DUPLICATE_GOVERNED_EVENT_ROW`.

For the supplied Week 4 sheet, the acceptance target is:

```text
expected_game_count=16
ready_pick_count=16
blocked_event_count=0
submission_ready=true
```

If any event lacks a valid governed package, the board becomes `PICKEM_BOARD_INCOMPLETE` or `PICKEM_BOARD_PARTIAL`; the system does not fabricate a selection from market price.

## Confidence Labels

Confidence labels are downstream descriptive bands only. They never alter probability:

```text
STRONG   >= 0.70
MODERATE >= 0.60
LEAN     >= 0.55
TOSS_UP  <  0.55
```

The separate `selection_volatility_band` is derived only from the governed home/away probability gap.

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

A new fitted total-points model would be Class C and requires challenger development, historical replay, calibration evidence, holdout/forward validation, and governed promotion before use.

## Tests

`artifacts/wow-engine/tests/test_v17_nfl_pickem_pool_optimizer.py` covers:

- 16 games in -> exactly 16 required picks out
- no sportsbook-price influence
- exact 50/50 tie preserving the controlling scorer choice
- typed scorer-failure preservation
- malformed governed package rejection
- non-final governance rejection
- selected-participant / probability consistency
- duplicate-event blocking
- unsupported strategy rejection
- tiebreaker fail-closed behavior

Local isolated reproduction before PR creation: `9 passed`.
