# MLB POST_IL_STARTER_FRAGILITY Challenger

Status: `EXPERIMENT_CREATED / PRODUCTION_INACTIVE`

Change class: **Class C — sporting probability behavior**.

This challenger is intentionally not wired into production scoring, calibration,
qualification thresholds, or publication. It may not be promoted without
historical replay, counterexample review, holdout/forward validation, regression
coverage, and governed WOW review.

## Triggering postmortem

The September 26, 2026 Minnesota–Texas postmortem identified a plausible starter
fragility pattern that was not adequately represented by surface recent-form
summaries: recent injured-list return, irregular rest, a relief-to-start
transition, workload/leash uncertainty, elevated baserunner traffic, and command
risk. One game is not sufficient evidence to change model mathematics.

## Hypothesis

For MLB starters returning from an IL stint or operating under an irregular
workload/role sequence, a fitted fragility feature family may improve the model's
representation of early-start downside without applying a universal probability
haircut.

The challenger must learn from historical outcomes. It must not encode the
Minnesota result as a manual penalty.

## Candidate feature family

Potential inputs, subject to source certification and leakage review:

- days since IL activation;
- IL reason category where safely and reliably structured;
- starts since activation;
- days of rest since prior appearance;
- deviation from the pitcher's normal rest distribution;
- relief-to-start or start-to-relief transition indicator;
- pitches and batters faced in recent appearances;
- recent pitch-count ceiling / observed managerial leash;
- recent WHIP / baserunner-traffic features over certified windows;
- walk rate and command indicators over certified windows;
- velocity delta versus pitcher baseline, where source quality permits;
- zone / chase / first-pitch-strike indicators, where certified;
- explicit workload restriction evidence when available from authoritative sources.

No narrative-only health inference may be converted into a numeric feature.

## Required experiment design

1. Build an MLB historical cohort with immutable pregame feature timestamps.
2. Define IL-return / irregular-role cohorts before looking at outcomes.
3. Compare current production champion versus challenger using identical splits.
4. Evaluate overall and cohort-level Brier score, log loss, calibration curves,
   lower-bound coverage, and ranking regret.
5. Review counterexamples where the fragility feature would have incorrectly
   downgraded a successful starter.
6. Run untouched holdout validation and then forward shadow validation.
7. Regression-check demonstrated champion strengths, especially stable healthy
   starters and non-IL workload patterns.
8. Confirm probability normalization, calibrated-bound validity, immutable
   prediction integrity, typed failure semantics, `V17_TERMINAL_REDUCER`, and
   `can_execute=false`.

## Promotion gates

Promotion requires evidence that the challenger improves calibration/reliability
without unacceptable degradation outside the target cohort. A single loss, a
small sample, or improved retrospective fit is insufficient.

Automatic certification: `false`

Automatic promotion: `false`

Production probability mutation: `false`

Terminal authority: `V17_TERMINAL_REDUCER`

`can_execute=false`
