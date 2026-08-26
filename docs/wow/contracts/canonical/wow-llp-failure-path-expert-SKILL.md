# Skill: wow.llp-failure-path-expert

## Purpose

Quantify the exact ways an outright favorite or underdog loses and incorporate those branches into the unconditional win probability.

## Governance

```text
lane_status=OUTRIGHT_FAILURE_PATH_MODEL
can_execute=false
```

## Core Principle

```text
P(win unconditional)=Σ P(regime_i) × P(win | regime_i)
```

A post-hoc narrative does not satisfy the skill.

## Favorite Regimes

At minimum model:

```text
NORMAL_ADVANTAGE_REALIZED
PRIMARY_STAR_OR_STARTER_FAILURE
TURNOVER_OR_COMMAND_COLLAPSE
SHOOTING_OR_FINISHING_VARIANCE
BULLPEN_OR_LATE_GAME_FAILURE
FOUL_OR_DISCIPLINE_FAILURE
DRAW_OR_OVERTIME_PATH_WHEN_APPLICABLE
WEATHER_OR_EVENT_DISRUPTION
```

## Underdog Regimes

At minimum model:

```text
BASELINE_COMPETITIVE_PATH
MATCHUP_ADVANTAGE_PATH
HIGH_VARIANCE_PATH
LATE_GAME_OR_FINISH_PATH
FAVORITE_PRIMARY_FAILURE_PATH
UNDERDOG_SELF_DESTRUCTION_PATH
DRAW_PATH_WHEN_APPLICABLE
```

## Exact-Market Rule

Moneyline failure paths must produce an outright loss under the exact settlement rules. Backdoor-cover risk is not a moneyline failure path unless it produces an outright comeback win by the opponent.

## Required Outputs

```text
regime_probabilities
conditional_win_probability_each_regime
unconditional_win_probability
largest_failure_path
largest_failure_probability
secondary_failure_paths
failure_path_score
market_alignment_status
```

## Hard Blocks

```text
NO_REGIME_DISTRIBUTION
MARKET_MISMATCHED_FAILURE_PATH
MATERIAL_FAILURE_PATH_UNMODELED
CONDITIONAL_PROBABILITY_PRESENTED_AS_UNCONDITIONAL
```

## Output

| Regime | Probability | P(Selection Wins | Regime) | Contribution |
|---|---:|---:|---:|

```text
unconditional_probability=
largest_failure_path=
failure_path_score=
result=
can_execute=false
```
