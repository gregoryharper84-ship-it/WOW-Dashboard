# V17 market-prior post-score migration experiment — 2026-09-24

## Classification

Class C guardrail investigation and scoped promotion candidate for issue #731.

The goal is architectural separation, not a new probability model: sportsbook
market evidence may support post-score market-role/value interpretation, but it
must not become an input to the governed fitted sporting probability.

## Production cohort audit

A production Supabase audit on 2026-09-24 found:

- 1,228 persisted rows for active MLB model version `MLB_V2C_SHARED_NB_2024_R1`;
- `market_prior_weight=0.0` on every active row;
- `market_prior_available=true` on zero active rows;
- exactly one non-zero market-prior-weight row in the full event-prediction
  table, an Aug-27 fixture (`fixture-mlb-v2` / `fixture-artifact`, weight 0.25),
  not the active production artifact.

The current MLB fitted feature-contract metadata contains no sportsbook/market
feature. The active scorer therefore has no certified probability dependency
that this migration is allowed to replace.

## NFL structural audit

The registered NFL publication/scoring path does not read `req.market_prior`.
Its fitted feature vector is composed of football pregame features such as rest,
EPA, success rate, turnovers, sacks, special teams, prior win rate and point
differential; it contains no sportsbook/odds feature.

## Counterfactual / replay interpretation

For the currently registered production specialists, a contradictory market
prior has no certified fitted-model channel:

- MLB: certified persisted weight is exactly zero across the active production
  cohort; the migration additionally clears the typed scorer request's
  `market_prior` field.
- NFL: the fitted feature/scoring contract has no market-prior input.

This is a structural equivalence proof for the current certified artifacts,
supported by the persisted MLB production cohort. It is narrower and stronger
than inferring equivalence from a handful of game outcomes. No historical market
result, closing price or sportsbook consensus is substituted for probability.

## Promotion boundary

The migration is limited to the currently registered team-event scoring stack.
Future sport specialists do **not** inherit permission to ignore or consume a
market prior. Any future fitted artifact that declares a market feature requires
its own governed certification/replay before this boundary changes.

## Implementation acceptance

The promotion candidate must prove in CI that:

1. the typed MLB fitted-model request receives `market_prior=None`;
2. TheRundown resolution occurs only after the sporting scorer returns;
3. exact market evidence is still available to downstream envelope/governance;
4. caller-vs-TheRundown favorite disagreement can only block market-relative
   rank eligibility, not alter calibrated probability;
5. provider failure cannot rewrite a model/scorer failure taxonomy;
6. three-way markets are never collapsed into a binary probability prior;
7. `probability_mutated_by_bridge=false` and `can_execute=false` remain true.

## Governance invariants

- exactly one fitted sport specialist owns sporting probability;
- no sportsbook price or no-vig consensus becomes sporting probability;
- calibration, lower bounds, uncertainty, thresholds and fitted artifacts are
  unchanged by this migration;
- `V17_TERMINAL_REDUCER` remains sole global terminal authority;
- execution remains disabled (`can_execute=false`).
