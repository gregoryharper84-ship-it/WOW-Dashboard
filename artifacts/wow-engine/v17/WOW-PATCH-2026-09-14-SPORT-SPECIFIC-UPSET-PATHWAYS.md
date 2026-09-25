# WOW-PATCH-2026-09-14-SPORT-SPECIFIC-UPSET-PATHWAYS

Status: BUILT — activation requires a separately trained, validated, sport-specific artifact.

## Decision

Upset evaluation is no longer defined by underdog probability alone. Each controlling
sport model may emit a `WOW_V17_UPSET_PATHWAY_V1` package before calibration. The raw
underdog probability must equal `sum(P(regime) * P(upset | regime))`. Favorite fragility
is independently exposed as the probability mass of fitted favorite-failure regimes.

Pathway breadth is the inverse-Herfindahl effective count of regime contributions. It is
diagnostic only: it never changes probability, calibration, admission, ranking, or stake.

## Hard boundaries

- Regime probabilities and conditional probabilities require a fitted artifact id and
  feature-schema hash.
- Market price, public/sharp action, revenge, momentum, streaks, narrative probability
  bands, and hand weights are forbidden probability sources.
- Mechanism families are sport-specific. A mechanism from one sport cannot silently
  enter another sport's package.
- The package must reconcile exactly to the controlling model's raw underdog probability
  before calibration. Calibration remains downstream and sport/model-family specific.
- Missing or invalid pathway artifacts return `UNAVAILABLE`; the engine must not invent
  diagnostics or alter the existing governing probability.
- Sportsbook price remains market classification/value evidence only.
- `can_execute=false` is invariant.

## Activation sequence

1. Label mutually consistent historical regimes using capture-time sporting inputs only.
2. Fit sport-specific regime and conditional-outcome effects, including validated
   interactions, under walk-forward splits.
3. Fit the favorite-fragility target independently.
4. Validate Brier/log loss, calibration, pathway stability, leakage, and subgroup drift.
5. Register as challenger; promote only through the existing champion/challenger gate.
6. Emit pathway package before the existing calibration and lower-bound stages.

The legacy narrative `gate_engine/moneyline/failure_path.py` is not a valid source for
this V17 package and cannot promote a candidate.
