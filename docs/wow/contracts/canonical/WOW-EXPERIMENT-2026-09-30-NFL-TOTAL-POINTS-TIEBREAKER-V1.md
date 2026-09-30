# WOW V17 NFL Total-Points Tiebreaker V1

Status: **CLASS_C_GOVERNED_REVIEW_REQUIRED**

Issue: #1090  
Branch: `experiment/nfl-total-points-tiebreaker-v1`

## Purpose

Provide a sporting-only full-game total-points projection for the NFL Pick'em sheet tiebreaker. This is **not** an over/under betting model and has no authority to publish `p_over`, `p_under`, betting qualification, rank eligibility, or execution.

Invariant values remain:

- `can_execute=false`
- `DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true`
- terminal authority remains `V17_TERMINAL_REDUCER`
- sportsbook totals, spreads, moneylines, implied probabilities, consensus projections, and generic LLM projections are prohibited as model features or substitutes.

## Data and point-in-time discipline

The replay uses regular-season rows from `wow_nfl_training_games`. Every target game is constructed only from prior completed games. The core estimator keeps current-season scoring and immediately prior-season scoring separate, then shrinks the current season toward the prior season with the existing eight-game V17 current-season reference horizon.

The tiebreaker specialist requires at least eight prior-season regular-season games for each team and is explicitly scoped to the 2026 artifact. A later season requires a new artifact/review rather than silently carrying the 2026 calibration forward.

2025 calibration/validation corpus SHA-256: `ef439cb952770a0b626a67024950a3e3807399adefdcccf6bb3e338b26f181fd`  
2026 forward corpus SHA-256 through Week 3: `0a3969b4f2721269394aec318814bb51332a4c967b7fcdbf34f61f307d631f1b`

## Challenger tournament

The tournament deliberately did not promote the most complex candidate.

| Candidate | 2025 validation | 2026 forward | Decision |
|---|---|---|---|
| Direct game-total ridge V1 | MAE 10.7797; RMSE 13.6202 | not promoted to forward | Rejected: worse than pre-specified empirical baseline on untouched 2025 validation |
| Team-score ridge V2 (alpha selected on 2024 only) | MAE 10.6900; RMSE 13.4846 | MAE 12.6067; RMSE 16.1161 | Rejected: slight 2025 gain did not survive 2026 forward evidence |
| Team-score ridge V2 refit through 2024, frozen alpha | 2025 used as post-validation calibration for the production-style refit | MAE 12.4990; RMSE 15.9913 | Rejected: still worse than the simpler forward baseline |
| Environment/roof ridge | 2024 calibration RMSE exceeded the baseline | — | Rejected before terminal holdout promotion |
| **Shrunk scoring/allowing empirical specialist** | **MAE 10.7303; RMSE 13.4892; bias -0.1632** | **MAE 12.0737; RMSE 15.4942; bias -0.3689** | **Promotion candidate for Pick'em tiebreaker only** |

The 2026 prior-season league-mean reference produced MAE 12.6720 and RMSE 15.6463. The selected specialist therefore retained better forward MAE and RMSE while remaining materially simpler than the rejected ridge challengers.

This is a model-selection result, not a claim that the tiebreaker can be predicted precisely. NFL game totals remain noisy.

## Interval calibration

Frozen 2025 residual quantiles for the selected estimator:

- q10 = `-16.8519050802139`
- q50 = `-0.274877450980391`
- q90 = `17.4090497737557`

The nominal 80% interval covered 79.41% of the 2025 validation set and 75.0% of the first 48 2026 forward games. The specialist publishes the interval next to the point estimate so downstream users do not mistake the point projection for certainty.

## 2026 Week 4 acceptance receipt

Using only completed 2025 regular-season data plus 2026 Weeks 1-3:

- Atlanta prior-season PF/PA: 22.3529 / 27.4706
- Atlanta 2026 PF/PA through Week 3: 26.6667 / 23.6667
- New Orleans prior-season PF/PA: 17.5882 / 23.6471
- New Orleans 2026 PF/PA through Week 3: 19.6667 / 28.6667
- current-season weight at three completed games: 3/8 = 0.375
- Atlanta @ New Orleans projected total: **44.2132352941**
- suggested integer tiebreaker: **44**
- 80% empirical interval: approximately **27.36 to 61.62**

No sportsbook total or market price was used to produce this projection.

## Runtime contract

`nfl_total_points_tiebreaker_specialist.py` owns only the Pick'em tiebreaker total projection. `nfl_pickem_runtime.py` resolves the unique event on the latest requested sheet date (for this sheet, Atlanta at New Orleans on October 5), loads only settled regular-season sporting outcomes, and returns the tiebreaker projection in the existing `runWowV17NFLPickemBoard` response.

The existing moneyline winner specialist remains the sole owner of NFL outright-win probabilities. The tiebreaker specialist cannot alter those probabilities, their calibration, their terminals, or betting eligibility.

## Promotion gate

Class C promotion is limited to `PICKEM_TIEBREAKER_ONLY`. The evidence does **not** authorize a general NFL totals betting lane. A future over/under model still requires line-conditioned probability modeling and separate calibration/certification.

Required before merge:

1. specialist and runtime unit tests pass;
2. existing Pick'em, NFL, typed-failure, editor-schema, and full WOW regression gates remain green;
3. adversarial review confirms no market substitution, probability-owner collision, execution permission, stale-season carry-forward, or silent tiebreaker ambiguity;
4. governed review approves the narrow tiebreaker-only promotion.
