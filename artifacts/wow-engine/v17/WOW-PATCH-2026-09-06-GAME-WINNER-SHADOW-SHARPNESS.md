# WOW V17 — Game Winner Shadow Sharpness Patch

Date: 2026-09-06
Status: SHADOW_ONLY
can_execute=false

## Objective

Improve the MLB Game Winner sporting probability itself without making the lane stricter and without changing downstream cash/value admission.

This patch is additive. It does not replace the active certified MLB Game Winner artifact and does not change any probability floor, NO_PICK rule, cash-single threshold, payout rule, portfolio rule, final-refresh requirement, immutable-write requirement, or terminal-reducer behavior.

## P0 cash/probability separation remains binding

The merged Game Winner cash-single P0 repair remains authoritative:

`LLP Game Winner probability -> exact PrizePicks payout -> exact fresh two-way/no-vig market -> cash-single promotion gate -> portfolio/exposure -> final refresh -> immutable pregame write -> V17 terminal reducer`

This patch operates only on the first element: sporting win probability.

The shadow challenger has:

- `MARKET_PRIOR_WEIGHT=0.0`
- sportsbook/odds/no-vig/payout/break-even/edge/CLV feature leakage blocked
- postgame/outcome leakage blocked
- `AUTOMATIC_PROMOTION_ALLOWED=false`
- `ADMISSION_POLICY_MUTATION_ALLOWED=false`
- `can_execute=false`

## Challenger architecture

The first shadow layer retains the existing structural Game Winner features and adds pregame tail/failure-path features:

- starter run variance differential
- starter catastrophe-rate differential
- starter early-hook differential
- starter third-time-through differential
- offensive cluster-rate differential
- scoreless-first-five differential
- bullpen run-variance differential
- bullpen 3+ run differential
- leverage-arm availability differential
- starter-to-bullpen handoff-risk differential
- lineup/platoon run-value differential
- park/weather run-environment state

The model uses one shared home-win probability and defines away probability as exactly `1 - P(home)`.

## Calibration and uncertainty

The shadow challenger uses:

1. a fitted logistic sporting model;
2. a chronologically later calibration cohort;
3. Platt/logit calibration;
4. bootstrap refits for candidate-specific uncertainty bounds.

If bootstrap uncertainty is unavailable, the code preserves the calibrated point estimate instead of inventing a universal haircut.

## Non-stricter invariant

Missing numeric feature values are imputed for shadow scoring. Feature coverage is emitted as observational telemetry only and is not a new candidate rejection gate.

The challenger does not know or consume Game Winner qualification thresholds. It scores every supplied hydratable row and returns probability output only.

## Promotion

Champion/challenger comparison is calibration-first:

- Brier score no worse;
- log loss no worse;
- calibration slope at least as close to 1.0;
- calibration intercept at least as close to 0.0.

Even if all four metrics pass, the result is `SHADOW_REVIEW_REQUIRED`; automatic promotion is prohibited.

Win rate and ROI are not promotion criteria.

## Data readiness observed before this patch

The connected production research store already contains the major historical base needed for training/feature materialization, including approximately:

- 9,900 multiseason MLB team-game rows;
- 4,700+ V2A game-feature rows across 2024/2025;
- 145,000+ retrospective player/game split rows;
- live/shadow lineup, bullpen, auxiliary and feature snapshots.

The richer MSD contract already exists, but its audit-snapshot table was empty at inspection time. This patch therefore creates the executable shadow probability layer without claiming the full MSD artifact has been fitted, certified, or promoted.

## Regression coverage

The added tests prove:

- no market/payout feature leakage;
- no postgame leakage;
- no admission-policy mutation;
- no automatic promotion;
- missing feature values do not filter a candidate;
- one shared home/away probability reconciles to 1;
- uncertainty intervals are candidate-specific;
- no universal haircut is invented when bootstrap uncertainty is absent;
- feature coverage is observational only;
- every supplied hydratable candidate receives a shadow probability;
- `can_execute=false` remains invariant.

## Explicitly unchanged

This patch does not modify `game_winner_cash_single_gate.py` and does not alter the merged P0 probability/cash separation.

The branch was synchronized to the current protected `main` before final verification.

It also does not claim backend production deployment or LIVE_GPT_EDITOR_SYNC.