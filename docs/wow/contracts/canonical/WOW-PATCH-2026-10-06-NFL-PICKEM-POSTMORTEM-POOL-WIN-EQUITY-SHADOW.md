# WOW PATCH — 2026-10-06 NFL Pick'em Settled-Week Retro + Pool-Win-Equity Shadow

Status: RESEARCH_SHADOW_IMPLEMENTED
Issue: #1434
Depends on: #1335 Week 4 fragility-review overlay
Serving mode: SHADOW_ONLY / POSTMORTEM_DIAGNOSTIC_ONLY
automatic_promotion=false
production_probability_mutation_allowed=false
production_pick_mutation_allowed=false
can_execute=false

## Why this patch exists

The final Week 4 pool standings exposed two different questions that must remain separate:

1. **Was the NFL sporting forecast/process wrong?**
2. **Was the submitted pick'em card optimized for winning a finite weekly pool?**

Those are not the same objective.

The final GH card finished 9-7. The weekly winner finished 13-3. The submitted GH card made no minority selections: it followed the strict pool majority in 14 games and a tied top share in the two 6-6 games. One loss, New England over Buffalo, was a unanimous pool miss and therefore did not reduce GH's relative standing.

The four-win gap to the weekly winner came from four head-to-head disagreements:

- JAX over CIN
- NYG over ARI
- DAL over HOU
- ATL over NO

This is evidence that **contest construction and sporting-probability quality must be graded separately**. It is not, by itself, evidence for an NFL fitted-model or calibration rewrite.

## What was learned

### Preserve

- Do not downgrade a strong governed side merely because it lost once.
- Shared consensus losses can be real forecasting misses while having zero relative pool cost.
- Sporting probability remains owned by the controlling NFL specialist.
- Pool ownership, contest standings, and opponent selections remain downstream evidence only.

### Diagnose

The Week 4 card had a contest-structure concentration problem:

```text
minority_pick_count=0
majority_follow_count=14
split_top_count=2
all_consensus_or_split_top=true
```

That means the card had little mechanism to separate from the field when several fragile/low-separation favorites lost.

### Learn

The active production objective:

```text
MAX_EXPECTED_CORRECT
```

is not identical to:

```text
MAXIMIZE_WEEKLY_FIRST_PLACE_OR_TIE_EQUITY
```

For a forced-winner pool, the second objective may rationally consider pick ownership **after** the governed sporting distribution exists.

The correct lesson is **not** "pick more underdogs." The earned hypothesis is:

```text
when a governed row is already TOSS_UP_REVIEW or MODEL_SIDE_FRAGILITY_REVIEW,
and the production side is materially over-owned,
and switching sides sacrifices only a small amount of expected correctness,
the minority side deserves shadow evaluation as a contest-strategy alternative.
```

### Do not overlearn

Week 4 alone does not justify:

- changing NFL model coefficients;
- changing calibration;
- lowering every favorite;
- fading consensus automatically;
- using pool popularity as sporting evidence;
- relabeling a winning minority result as proof it was the correct pregame choice.

## Implemented changes

### 1. Settled-week postmortem diagnostic

New module:

```text
artifacts/wow-engine/v17/nfl_pickem_postmortem.py
```

It accepts the fully settled pool card and returns wins/losses and rank, gap to winner, majority/split/minority counts, unanimous shared losses, exact winner-vs-user disagreement events, optional tiebreaker realized error, and explicit learning actions.

Hard output:

```text
production_probability_patch_earned=false
production_probability_mutation_allowed=false
automatic_promotion=false
can_execute=false
```

Model attribution still requires immutable pregame prediction receipts. The final pool sheet alone cannot prove a probability-model defect.

### 2. Pool-win-equity shadow challenger

New module:

```text
artifacts/wow-engine/v17/nfl_pickem_pool_win_equity_shadow.py
```

This is not a new sporting model. It consumes an already-valid production pick'em row, its governed probabilities, its Week 4 learning review class, pool pick share, and pool size. The production pick remains immutable.

Shadow eligibility currently requires all of the following diagnostic conditions:

```text
review_class in {TOSS_UP_REVIEW, MODEL_SIDE_FRAGILITY_REVIEW}
production side is more heavily owned than opponent
ownership_gap >= 0.15
expected_correct_sacrifice_if_switched <= 0.10
not HIGH_CONFIDENCE_HOLD
```

These are **research thresholds**, not certified production policy.

The shadow output may expose production_pool_pick, shadow_pool_pick, governed probabilities, pool shares, ownership gap, expected-correct sacrifice, and a shadow differentiation signal.

It always preserves:

```text
production_pool_pick_unchanged=true
sporting_probabilities_unchanged=true
automatic_promotion=false
production_pick_mutation_allowed=false
production_probability_mutation_allowed=false
can_execute=false
```

### 3. Strong-side preserve guardrail

A HIGH_CONFIDENCE_HOLD cannot become a shadow differentiation candidate merely because the field heavily owns it. This protects against learning the wrong lesson from a unanimous favorite upset.

### 4. No blind contrarianism

A side is not eligible merely because it is unpopular. A switch is blocked when the expected-correct sacrifice is too large, the ownership gap is too small, the row is not already fragile/toss-up, or the production side is not over-owned.

A 50/50 pool split has no ownership leverage by itself even if the game is a toss-up.

## Week 4 regression fixture

The new regression test reconstructs all 12 final Week 4 entries and all 16 settled winners.

Required GH outputs:

```text
user_wins=9
user_losses=7
best_wins=13
gap_to_best=4
wins_rank=7
majority_follow_count=14
split_top_count=2
minority_pick_count=0
all_consensus_or_split_top=true
unanimous_shared_loss_count=1
pool_strategy_shadow_review_earned=true
production_probability_patch_earned=false
```

Required winner comparison:

```text
weekly_winner=Jay
disagreement_count=4
winner_gain_count=4
user_gain_count=0
net_disagreement_swing=4
winner_gain_events=[JAX@CIN, ARI@NYG, DAL@HOU, ATL@NO]
```

## Validation required before any production strategy change

This patch does not enable POOL_WIN_EQUITY as a production strategy.

Promotion requires replay/forward evidence across multiple independent weeks and preferably multiple pool sizes. Compare incumbent vs shadow on average correct picks, weekly rank percentile, first-place frequency, tie-for-first frequency, expected payout when known, deliberate differentiator count, downside from excessive contrarianism, and unchanged calibration integrity.

Required promotion decision:

```text
challenger_win => SHADOW_REVIEW_REQUIRED
challenger_win != automatic production promotion
```

## Governance invariants

```text
sporting probability and contest strategy remain separate
pool ownership cannot alter governed probability
postgame outcome cannot enter a pregame probability
immutable prediction receipts remain required for model-quality attribution
V17_TERMINAL_REDUCER remains sole global terminal authority
automatic_promotion=false
can_execute=false
```