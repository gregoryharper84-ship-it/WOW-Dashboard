# MLB 1IP integer-line and publication-bound experiment — 2026-09-22

Status: **EXPERIMENT_CREATED**  
Change class: **Class C — research/challenger only**  
Production promotion: **NOT AUTHORIZED**  
`can_execute=false`  
`DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true`

## Trigger

A live PrizePicks board on 2026-09-22 contained MLB `1ST_INNING_PITCHES_THROWN` lines at `16.0`. The active controlling artifact is governed by an exact-line certification policy and currently validates `[11.5, 13.5, 14.5, 15.5, 16.5, 17.5, 19.5, 21.5]`. Therefore `16.0` correctly fails closed today as `MLB_1IP_LINE_OUTSIDE_CERTIFIED_SUPPORT`.

The active artifact is also `PROSPECTIVE_CERTIFIED` hold-only with `probability_publishable=false` / `rank_eligible=false`. Its lower-bound status is inherited from the aggregate-width shift and has not been separately coverage-validated for publication authority.

## Hypotheses

1. **Integer line support:** a fitted 1IP distribution may be able to support an exact `16.0` market only after explicit push-aware validation. No interpolation from `15.5` or `16.5` is permitted.
2. **Publication authority:** model probabilities and calibrated lower bounds may be eligible for publication only if a disjoint validation demonstrates the required calibration and lower-bound coverage. A passing point-probability validation alone is insufficient.

## Required challenger/replay design

Use the existing registered controlling 1IP fitted family as the baseline. Do not replace it with sportsbook implied probability, public projections, generic Elo, or LLM judgment.

For `16.0`, build a push-aware exact-line challenger that explicitly estimates and validates `P(MORE 16.0)`, `P(LESS 16.0)`, and the probability mass at exactly 16 pitches according to the target platform's settlement semantics. Preserve probability normalization and do not coerce push mass into either directional probability.

Replay on a disjoint chronological holdout not used to fit or select the active artifact. At minimum report:

- sample size and source-gap rate;
- Brier score and calibration error for each directional outcome under the settlement definition;
- discrimination / probability spread sufficient to prove the model is not a constant-rate proxy;
- push frequency and normalization checks;
- comparison against the existing certified sentinel lines `15.5` and `16.5` without degrading their previously demonstrated behavior;
- calibrated lower-bound empirical coverage by meaningful probability bins and direction;
- counterexample review for pitchers / lineup states / failure paths producing the largest errors;
- data-freshness and exact-line identity integrity.

## Promotion gates

No production model, artifact, calibrated lower bound, exact-line support set, threshold, or probability publication flag may change from this experiment alone.

A promotion recommendation requires:

1. historical replay complete;
2. counterexample review complete;
3. disjoint holdout or forward validation complete;
4. regression checks on all currently certified 1IP lines;
5. separate lower-bound coverage evidence sufficient for publication authority;
6. independent governed review of the challenger packet;
7. explicit promotion decision creating a new immutable artifact/version rather than editing the active artifact in place.

Until those gates pass, production behavior remains:

- `16.0` → `REJECT_OOD / MLB_1IP_LINE_OUTSIDE_CERTIFIED_SUPPORT`;
- existing supported lines may complete sporting-model evaluation but remain under the active artifact's hold-only publication ceiling;
- `can_execute=false` remains invariant.
