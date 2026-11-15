# WOW V17 Nov 14 Market + Combat Challenger

**Class C — diagnostic-only and non-serving. No model/parameter/floor/deployment changes.**
**can_execute=false; V17_TERMINAL_REDUCER remains sole terminal authority.**

The Saturday Nov 14 cross-sport card lacked linked immutable Stage-20 predictions.
It is **POST_EVENT_RETROACTIVE / INSUFFICIENT_SAMPLE**, not a fitted-model
calibration cohort. Do not backfill sporting forecasts or infer model reliability.

## Two scoped hypotheses

1. The market dependency diagnostic compares frozen, settled **same-sport,
   same-role** adverse residual tails in a predesignated development fold.
   It flags market_prior_weight > 0.50 but NEVER replaces the incumbent
   calibrated_probability or calibrated_lower_bound. An empirical tail stress
   floor is diagnostic; it is NOT a calibrated interval. This exploratory
   approach may be confounded by probability distribution differences and
   must pass covariate-matched chronological holdouts before consideration.
   Historical records must include a unique event ID, immutable pregame issuance,
   kickoff and settlement timestamps; only one row per event counts.
   Incumbent PrizePicks cash-single market-dependency rejection stays active.
2. The combat style mixture scores mutually exclusive fitted MMA/boxing regimes
   using sum(weight * conditional_win_probability). Required upstream style
   features: takedown_defense_vs_chain_wrestling_delta (MMA) and
   high_guard_pressure_jab_absorption (boxing). Stress floors omit uncertainty
   in weights and therefore are NOT calibrated lower bounds. A failure score
   >0.20 is a single adverse-regime realization probability hypothesis flag,
   distinct from expected adverse loss mass; not production policy.

## Run

    python -m pytest -q artifacts/wow-engine/v17/experiments/nov14_market_style_challenger/test_challenger.py

Synthetic fixtures only. No DB/network calls, no dependency installations,
no settlement or probability mutations, no trading. Unit tests verify
arithmetic, leakage barriers, bounded inputs, role isolation and fail-closed
behavior. They do NOT establish out-of-sample lift.

## Preserve -> Refine -> Regression Check

Preserve Denver/Iowa/USA/Villanova/Texas/Svitolina winner/upset evidence without
overfitting, sport+market_role reliability lanes, existing min-sample gate,
exact immutable receipt chronology, price-vs-probability separation and all
terminal safety controls. No global probability haircut or qualification
floor tightening.

Before a Class B publication boundary is considered, independently test that
a price-only heavy favorite cannot be labeled certified "strong confidence",
that spreads and totals cannot inherit moneyline confidence, and that
same-event card risk is measured as dependent rather than independent legs.

Before ANY Class C promotion, require independently verified, immutable pregame
cohort, one event identity per game, exact market/role, train-calibration-
chronological holdout separation; Brier/log loss/calibration + uncertainty
comparison against same-event incumbent and market baselines; full regression,
independent QA/SIRT and protected release. Do not use November 14 retrospective
results for fitting, picking hyperparameters or constructing a fake forward test.

## Live receipt audit at preparation

Read-only WOW validation Supabase inspection identified no Nov 14 matching
rows in wow_postmortem_runs/legs, wow_recommendation_records or the inspected
wow_event_predictions, wow_nfl_event_predictions and wow_predictions ledgers.
This is not evidence of absence in every possible system. This experiment
does not write retroactive rows.

**This PR is non-serving engineering research completion, not sporting model
certification or deployment.**
