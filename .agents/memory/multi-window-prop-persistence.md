---
name: Multi-window prop persistence patch
description: Patch #19 — persistence score, window agreement, threshold cushion, same-player mutex, MLB binomial hit model, WNBA points/assists/threes distribution models.
---

# Multi-Window Prop Persistence & Distribution Audit (Patch #19)

**Patch ID:** `WOW-PATCH-2026-08-01-MULTI-WINDOW-PROP-PERSISTENCE-AND-DISTRIBUTION-AUDIT`
**Precedence:** 98
**Patch count after this:** 19

## Key modules

- `gate_engine/prop_persistence.py` — persistence score (weights: role_matched=35%, season=25%, L10=15%, L20=15%, L5=10%), window agreement classification, threshold cushion (mean/median/std/25th-pct), inflation audit (8 checks), RECENT_FORM_DIVERGENCE at |L10−season| ≥ 20pp
- `gate_engine/same_player_mutex.py` — SAME_PLAYER_SHARED_THESIS cluster detection; primary = highest calibrated_lower_bound; override: `joint_dependence_modeled=True`; separate `check_same_game_correlated_legs()` for multi-player event groups
- `gate_engine/mlb/hit_probability_model.py` — `P(1+ hits) = 1−(1−p)^n`; platoon adjustments; batting_order PA priors; `score_zero_point_five_hits()` convenience wrapper with calibration_floor
- `gate_engine/wnba/points_model.py` — Normal(μ,σ) model; blowout_risk, foul_trouble_discount, opponent_def_rank, primary_teammate_avail
- `gate_engine/wnba/assists_model.py` — Poisson(λ) model; uses discrete CDF for λ<4; on_ball_role, pace, turnover_risk
- `gate_engine/wnba/threes_model.py` — Binomial(n,p) model; always emits `high_variance_warning`; n and p resolved from caller/games/league-average in priority order

## Design rules

- All persistence outputs affect **research_priority only** — never override calibrated_lower_bound
- Historical hit rate must never be published as model probability
- `can_execute=False` unconditional on all modules

## New labels added to labels.py

`RECENT_FORM_DIVERGENCE`, `OUTLIER_OR_ROLE_AUDIT_REQUIRED`, `SAME_PLAYER_SHARED_THESIS`, `NEXT_DAY_PREVIEW`, `LAW_OF_AVERAGES_SUPPORT`, `HOT_STREAK_AS_PROBABILITY`, `ONE_GAME_SAMPLE_INSUFFICIENT`

## Bug fixed during development

`compute_threshold_cushion` hit-rate calculation initially used a double-`if` clause in a generator expression (`if condition1 if condition2 else`) which is a SyntaxError in Python. Fixed by hoisting the direction check to a `over = ...` variable and using a ternary inside the generator.

## Impact on existing tests

- Patch count bumped 18 → 19 in both `test_governance_resilience_acceptance.py` and `test_patch_portfolio_stage2a.py`.
