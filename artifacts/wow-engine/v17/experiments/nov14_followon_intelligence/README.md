# WOW V17 November 14 — NBA fatigue, cross-market isolation & dual-gate follow-on

Status: SOURCE IMPLEMENTED / CLASS C RESEARCH-ONLY / NO PRODUCTION INTEGRATION. Connected owner: LLP_TEAM_BETTING_ENGINE and Project Hub #2076. Follows merged #2675. All tests are synthetic; no claim of calibrated, fitted model lift or live betting edge.

## Purpose and existing owners

1. **NBA fatigue / 3-point variance:** Pure nba_fatigue_three_point_shadow produces an interaction of opponent B2B * verified travel flag * defensive index * pace. Existing basketball_team_event_specialist retains the fitted feature schema. Existing PR #2655 is the owner of verified time-correct 13-sport schedule/travel chronology; this function consumes an already-frozen snapshot, NOT a rival schedule engine. It computes the two-category Dirichlet-multinomial (Beta-binomial) variance of made threes from prefit alpha/beta and projected attempts; absent an independently supplied covariation estimate, it will not compute combined margin variance, and never changes the model's mean, winner probability, or lower bound. No blanket fatigue penalty and no posterior derived from the November 14 Bulls/Wolves result.

2. **Cross-market isolation:** audit_same_event_cross_market is a research-only supplement to existing v17.portfolio_exposure_gate and v17.slip_portfolio_optimizer restrictions. It groups by exact sport+official_event_id, checks distinct specialist evidence for SPREAD and TOTAL (including signed exact line, market family, timestamp and source IDs) and holds all mixed-ML/SPREAD/TOTAL same-event combinations with an unquantified joint-dependence diagnostic. Callers cannot make correlation vanish with a claimed joint receipt; the joint model must be authenticated and validated in a separate controlled integration. Neither an MLB/NBA event ID collision nor a moneyline win should give a spread/total a favorable grade.

3. **Dual qualification:** dual_gate_strong_confidence_shadow preserves probability leaderboard eligibility independently of strong-confidence objective admission. It requires a current approved threshold policy for the probability tier and independently checks the *executable* price, fee, price age, model dependence, exact event/side, authenticated two-way market context and friction-adjusted lower-bound edge. Arithmetic uses American odds converted to net profit per unit: break_even = 1 / (1 + profit_after_fee); LB_edge = calibrated_LB - break_even - friction_buffer. The no-vig market probability is diagnostic context, not an executable break-even substitute. Existing v17.game_winner_cash_single_gate and open objective/card separation PR #2333 remain controlling; this module does not supersede them.

## Freeze and missingness

All snapshot timestamps are timezone aware. For NBA, both shot priors are required to be available at the feature freeze. Missing shot covariance means unknown combined margin variance, not zero covariance. A raw source string/hash is a *claimed* provenance, not authenticated external verification. Model certification, certified lower bounds, approved threshold policy and exact book quotes require external authority on any future serving path. Unit test fixtures do not establish source authenticity.

## Run focused regressions

From repository root:

    python -m pytest -q artifacts/wow-engine/v17/experiments/nov14_followon_intelligence/test_followon.py

CI should also run existing portfolio, cash-single and basketball specialist test suites before Class B integration. Preserve the current fitted feature schema and protected V17_TERMINAL_REDUCER checks.

## Immutable historical performance gate

- Backtest requires genuine, linked and independently verified pregame Stage-20 receipts, exact event IDs/side/line/market periods, and independent official settlements.
- Design chronological TRAIN -> CALIBRATION -> UNTOUCHED HOLDOUT by sport+market+role. For the NBA effects use Brier/log loss and calibration on real B2B/travel subgroups; compare correctly matched incumbent baseline and challenger, not a one-slate historical adjustment.
- For shot models test predicted 3PT and margin distribution calibration separately, including tail coverage on three-point-volume stress matches; do not promote a mere increase in variance as a genuine calibrated lower bound.
- Cross-event independence and cross-market joint model accuracy require independent authenticated joint receipts; missing joint model remains HOLD. No unvalidated product of marginal probabilities.
- Do not promote based on Nov 14: sample size and Stage-20 provenance insufficient.
- Preserve can_execute=false, no auto trading, no automatic Pick'em submission and V17_TERMINAL_REDUCER as sole final authority.
- Do not alter existing approval floors, monetary buffers, model artifacts, odds, rank flags or stored probabilities.
- Existing overlapping work: #2655 (schedule chronology), #2333 (rank/card separation), #2675 (market-dependency and combat-style), #2076 (single LLP team/event owner).

## Acceptance levels

- **Code implemented:** shadow diagnostic functions plus fail-closed synthetic tests exist in repo.
- **Ready for merge:** focused and adjacent CI green at exact SHA, independent review, authorized protected-main merge.
- **Not established:** real model lift, calibrated intervals, officially certified strong-confidence card, verified cross-market portfolio joint models, deployment or user-app synchronization.

Do not call this research packet a full model improvement until real data and held-out validation substantiate the gain.

## Branch synchronization

The research branch was reconciled with current main without overwriting later changes to engineering adoption files. This preserves the experiment-only diff and requires a fresh exact-head CI acceptance after synchronization.
