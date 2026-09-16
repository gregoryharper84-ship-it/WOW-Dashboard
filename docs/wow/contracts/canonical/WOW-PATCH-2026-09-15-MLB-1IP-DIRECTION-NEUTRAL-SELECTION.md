# WOW-PATCH-2026-09-15-MLB-1IP-DIRECTION-NEUTRAL-SELECTION

## Purpose

Remove selection-layer directional bias from MLB `1ST_INNING_PITCHES_THROWN`
without changing the fitted probability model, calibration lifecycle, exact-line
support, publication authority, or `can_execute=false`.

## Controlling rule

When a current board exposes both `MORE` and `LESS` for the same exact 1IP
pitcher/line, both directions must reach the same canonical `/score-pick-request`
path before the line can participate in direction comparison.

```text
same player
+ same official event
+ same stat
+ same exact line
+ same source snapshot
=> evaluate offered MORE and LESS
=> preserve each native model/terminal package
=> compare governed calibrated_lower_bound
=> stronger lower bound is the modeled direction for comparison
```

There is no default `MORE` preference and no default `LESS` preference.

If one offered direction is missing, not evaluated, malformed, OOD, stale, or
otherwise lacks the governed lower bound required by the lane, the pair is
`BIDIRECTIONAL_INCOMPLETE`. The surviving side cannot win by default.

## No post-model directional haircut

The active player-conditioned 1IP scorer already produces both `P_MORE` and
`P_LESS` from one fitted distribution. Selection/orchestration must not apply a
second manual direction penalty after that governed package is returned.

Legacy documentation that describes fixed/manual `LESS`-only deductions,
tier downgrades, or ceilings is diagnostic context only for the active V17
player-conditioned serving path unless the active certified artifact/calibrator
explicitly owns that numerical adjustment.

This patch does not prohibit real directional asymmetry. The fitted distribution
may legitimately produce different probabilities and lower bounds for MORE and
LESS. It prohibits assistant/orchestration preference that is not part of the
certified model/calibration package.

## Canonical Action audit

The canonical prop Action result must expose:

```text
source_1ip_lines
bidirectional_expected
bidirectional_complete
single_direction_offers
more_preferred
less_preferred
ties
incomplete_pairs
per-line MORE/LESS model status and calibrated lower bound
comparison winner by calibrated lower bound
```

Comparison output is audit/research metadata. It must preserve, not override:

```text
terminal_status
terminal_label
probability_publishable
rank_eligible
can_execute=false
```

A comparison winner is never automatically a published or rank-eligible pick.

## Exact-line support

Existing exact-line governance is unchanged.

Unsupported lines remain deterministic OOD/rejection. Adjacent lines cannot
authorize or reconstruct an unsupported exact PrizePicks line.

## Postmortem learning

Grade MLB 1IP by direction as separate cohorts:

```text
direction = MORE
direction = LESS
```

For each supported cohort use immutable exact pregame predictions and report,
when sample size permits, `n`, mean probability, observed hit rate, Brier,
log loss, calibration bias, and lower-bound reliability.

A single loss, single day, or thin directional cohort cannot justify a global
haircut or ban. Apply the V17 preserve -> diagnose -> learn -> patch-only-when-earned
contract.

## Regression requirements

1. LESS wins when its governed lower bound exceeds MORE.
2. MORE wins when its governed lower bound exceeds LESS.
3. A two-direction offer with one missing side is incomplete, not auto-selected.
4. OOD/rejected pairs do not manufacture a selection.
5. A true one-direction platform offer is not expanded synthetically.
6. Comparison never mutates probability, terminal, publication, or rank fields.
7. No direction is preferred by ordering or hard-coded tie break.
8. `can_execute=false` remains invariant.
