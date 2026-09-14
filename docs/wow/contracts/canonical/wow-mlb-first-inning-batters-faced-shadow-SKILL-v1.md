# Skill: wow.mlb-first-inning-batters-faced-shadow

## Revision

```text
revision = V1.0_SHADOW
runtime_generation = V17_ACTIVE
lane_status = SHADOW_VALIDATION_ONLY
can_execute = false
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS = true
```

## Purpose

Evaluate whether WOW's existing MLB first-inning event-tree strength transfers to the pitcher **1st Inning Batters Faced** market without weakening V17 governance or inventing a new probability source.

This is a shadow challenger, not a production prop specialist. It derives an exact batters-faced line probability only from a valid upstream MLB 1IP event-tree probability package that already contains:

```text
P(BF=3)
P(BF=4)
P(BF>=5)
```

The upstream controlling specialist remains:

```text
wow.mlb-first-inning-pitch-count-expert
```

The shadow adapter may not replace, relabel, or override that specialist.

## Why this lane is the first expansion target

The current 1IP model already models the first-inning batter-count event tree as a load-bearing latent variable before converting it into total pitches. Batters Faced therefore reuses the strongest existing first-inning structure instead of introducing a disconnected model family.

The intended progression is:

```text
existing 1IP event tree
→ governed P(BF) distribution
→ exact BF threshold mapping
→ immutable shadow prediction
→ settlement
→ calibration / reliability study
→ certification decision
```

No production promotion occurs merely because the derived point probability looks strong.

## Initial exact-line support

The current upstream contract exposes only three BF buckets:

```text
BF = 3
BF = 4
BF >= 5
```

That supports these exact line mappings without interpolation:

```text
3.0
3.5
4.0
4.5
```

Primary PrizePicks-style half-point targets:

```text
3.5
4.5
```

Exact mapping:

```text
line 3.5:
  P(MORE) = P(BF=4) + P(BF>=5)
  P(LESS) = P(BF=3)

line 4.5:
  P(MORE) = P(BF>=5)
  P(LESS) = P(BF=3) + P(BF=4)
```

Integer lines preserve push mass:

```text
line 3.0:
  P(MORE) = P(BF>=4)
  P(PUSH) = P(BF=3)
  P(LESS) = 0

line 4.0:
  P(MORE) = P(BF>=5)
  P(PUSH) = P(BF=4)
  P(LESS) = P(BF=3)
```

## Fail-closed unsupported lines

Do not score 5.5 or higher from the current coarse upstream distribution.

Reason:

```text
P(BF>=5) does not identify P(BF=5) separately from P(BF>=6)
```

Therefore:

```text
5.5+ => BF_LINE_UNSUPPORTED_BY_COARSE_BUCKETS
```

No interpolation, narrative estimate, recent hit rate, or sportsbook price may fill this gap.

Future support for 5.5+ requires the upstream event tree to expose a fuller discrete BF PMF such as:

```text
P(BF=3)
P(BF=4)
P(BF=5)
P(BF=6)
P(BF>=7)
```

or another certified equivalent.

## Input contract

Required upstream shadow input:

```text
model_evaluated = true
P_BF_3
P_BF_4
P_BF_GE_5
model_used if available
model_timestamp if available
```

The distribution must be numeric, bounded in [0, 1], and normalized within a narrow transport-rounding tolerance.

The adapter may normalize only small transport rounding drift. Material non-normalization fails closed.

## Output contract

Every shadow prediction must include:

```text
stat_type = 1ST_INNING_BATTERS_FACED
shadow_adapter
controlling_specialist
upstream_bf_schema
upstream_model_used
upstream_model_timestamp
line_value
direction
P_BF_3
P_BF_4
P_BF_GE_5
P_MORE
P_LESS
prob_push
selected_probability
calibrated_probability = null
calibrated_lower_bound = null
calibration_status = SHADOW_UNCALIBRATED
model_status = SHADOW_CHALLENGER
terminal_label = RESEARCH_INTEREST
rank_eligible = false
probability_publishable = false
immutable_forward_log_required = true
promotion_required_before_governed_use = true
can_execute = false
```

## Probability governance

Hard rules:

```text
shadow selected_probability != governed calibrated probability
shadow selected_probability != governed calibrated lower bound
RESEARCH_INTEREST != MODEL_QUALIFIED
```

The derived probability may be stored and graded as an immutable research forecast, but it may not appear on an official V17 probability leaderboard until a dedicated calibration/certification package exists.

Do not use sportsbook implied probability, recent BF hit rate, or generic reasoning as a substitute.

## Forward-validation protocol

Every eligible pregame board row should be frozen before event start with:

```text
prediction_id
candidate_id
event_id
pitcher
opponent
event_start
line
direction
upstream_1ip_prediction_id when available
upstream_model_timestamp
P_BF_3
P_BF_4
P_BF_GE_5
selected_probability
source timestamps
created_at
```

After settlement append:

```text
official_batters_faced
settled_result
settlement_source
settlement_timestamp
brier
log_loss
```

A materially different line or direction is a different prediction.

## Validation metrics

Evaluate at minimum:

```text
n
mean predicted probability
observed hit rate
Brier score
log loss
calibration bias
reliability by probability bin
reliability by exact line
reliability by MORE vs LESS
reliability by pitcher/opponent regime when sample supports it
```

Hit rate alone is not sufficient for promotion.

Comparison should include simple baselines where available, such as recent empirical BF frequency, but those baselines remain validation comparators rather than governed probabilities.

## Promotion evidence

Do not promote from one day, one slate, or a handful of wins.

Minimum evidence discipline before certification review:

```text
>= 100 settled immutable shadow predictions overall for stronger calibration evidence
>= 30 settled predictions in any exact line/direction cohort proposed for promotion
chronological out-of-sample evaluation
no training/test leakage
pregame predictions frozen before settlement
Brier/log-loss comparison against declared baselines
calibration review
counterexample review
regression suite pass
strength-preservation check for existing 1IP pitches lane
```

These are minimum review gates, not an automatic production-promotion rule.

## Required regression tests

1. 3.5 MORE equals P(BF>=4).
2. 3.5 LESS equals P(BF=3).
3. 4.5 MORE equals P(BF>=5).
4. 4.5 LESS equals P(BF<=4).
5. Integer lines preserve push mass.
6. 5.5+ fails closed under the three-bucket BF schema.
7. Missing upstream P(BF) fields fail closed.
8. Materially non-normalized upstream distribution fails closed.
9. Upstream `model_evaluated=false` cannot produce a shadow prediction.
10. Shadow output never creates calibrated probability or calibrated lower bound.
11. Shadow output is never rank eligible or probability publishable.
12. Push/void outcomes are not silently graded as losses.
13. `can_execute=false` remains invariant.
14. Existing 1IP pitches-thrown production behavior is unchanged.

## Next expansion after validation

If Batters Faced validates, the next candidate should be **1st Inning Strikes Thrown**, because the existing event tree can be extended from:

```text
BF distribution
× pitches per plate appearance
```

to:

```text
BF distribution
× pitches per plate appearance
× pitcher strike probability
× opponent take/swing/contact behavior
```

That future lane must receive its own fitted/calibrated certification rather than inheriting Batters Faced or Pitches Thrown calibration.

## One-line definition

**WOW MLB 1st Inning Batters Faced Shadow reuses the governed 1IP event-tree P(BF) distribution to create exact, immutable research forecasts for supported BF lines, then requires forward calibration and certification before any model-qualified use.**
