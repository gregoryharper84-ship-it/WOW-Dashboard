═══════════════════════════════════════════════════
WOW PATCH — FREE-HOST PROBABILITY & CALIBRATION ENGINE
═══════════════════════════════════════════════════

PATCH ID:          WOW-PATCH-2026-08-26-FREE-HOST-PROBABILITY-ENGINE
REVISION:          v2 — ratified methodology incorporated (ChatGPT, 2026-08-26)

BASE SPEC:         WOW v16 Clean Core / Framework v2.2.0

STATUS:            APPROVED — ANALYTICAL METHODOLOGY
                   IMPLEMENTATION — PENDING (10-point deployment gate,
                   see below; governed_probability_capability remains
                   UNAVAILABLE until all 10 pass)

─────────────────────────────────────────────────
RATIFIED METHODOLOGY (supersedes v1's [DECISION REQUIRED] placeholders)
─────────────────────────────────────────────────

8B.1 Failure-Path Matrix — Mutually Exclusive Regimes + Cause Tags

PRIMARY REGIME (exactly one per simulated start; Sum P(regime) = 1.000):
    R0 NORMAL_EFFECTIVE_OUTING
    R1 INEFFICIENT_SURVIVING_OUTING
    R2 EARLY_EXIT_PERFORMANCE
    R3 EARLY_EXIT_HEALTH_OR_WORKLOAD
    R4 PLANNED_RESTRICTION_OR_SHORT_LEASH
    R5 GAME_DISRUPTION

CAUSE TAGS (zero or more, non-exclusive, attached to a regime instance):
    COMMAND_COLLAPSE, OPPONENT_EXTENSION, MANAGER_HOOK_PRESSURE,
    PITCH_EFFICIENCY_FAILURE, BULLPEN_READINESS_PRESSURE,
    VELOCITY_OR_HEALTH_WARNING, CONTACT_EXTENSION, PATIENCE_EXTENSION,
    WEATHER_OR_DELAY, DEFENSIVE_EXTENSION

Regime probability estimation: empirical-Bayes Dirichlet-multinomial.

    alpha_r = kappa * P_cohort(r)
    P(r | pitcher) = (n_pitcher_r + alpha_r) / (N_pitcher + sum(alpha_r))

    cohort match: league, starter/reliever role, season era, handedness
    (where material), workload band, market family

    kappa: prefer marginal-likelihood estimate from historical training
    data; bounds 5 <= kappa <= 30; fallback kappa = 12 if optimization
    unstable

Current-game info (injury, post-IL, rest, pitch cap, velocity warning,
opener role, weather delay risk): NEVER a manually invented probability
adjustment.
    validated coefficient available -> apply learned adjustment
    no validated coefficient        -> widen uncertainty / change regime
                                        eligibility / HOLD
    material contradiction          -> block

Conditional prop model (inside each regime):
    innings/BF distribution x K-per-BF distribution x pitch-count/leash
    distribution -> P(prop | regime)
    P(prop) = Sum [P(regime) x P(prop | regime)]
    >= 50,000 Monte Carlo draws per candidate, deterministic seed recorded

8B.2 Calibrated Probability Ledger — Amended Schema

Additional fields beyond v1 draft:
    regime_model_version, regime_probabilities_json, regime_probability_sum
    primary_failure_path, failure_cause_tags
    simulation_seed, simulation_draws
    effective_sample_size
    calibration_status, calibration_training_n, calibration_parent_cohort,
    calibration_fit_start, calibration_fit_end
    market_prior_available, market_prior_quality, market_prior_weight_source
    probability_publishable, probability_ceiling
    data_gaps, blockers

Hard constraints:
    abs(sum(regime_probability) - 1) <= 0.000001
    0 < raw_model_probability < 1
    0 < lower_bound <= calibrated_probability <= upper_bound < 1
    simulation_draws >= 50000 for live prop scoring
    source_snapshot_id IS NOT NULL
    prediction row immutable after event start
    any failure -> probability_publishable = false, no silent repair

8B.3 Market-Prior Blending — Zero Weight at Cold Start

    market_prior_weight = 0.00 at launch, even with valid exact two-way
    no-vig. Still record independent_model_probability,
    market_prior_probability, market_difference, market_timestamp,
    market_quality — but do not mix them into calibrated_probability yet.

    Learning gate: >= 200 verified settled predictions in the applicable
    parent calibration cohort, out-of-fold optimization against
    log loss/Brier.

    Blend method (once eligible): log-odds stacking, not arithmetic
    averaging.
        logit(p_blend) = (1-w)*logit(p_independent) + w*logit(p_market)
        0.00 <= w <= 0.35  (hard cap — prevents WOW from quietly
        becoming a sportsbook copy)

    If exact two-way no-vig unavailable: market_prior_probability = NULL,
    market_prior_weight = 0. One-sided alternate price populates
    reference_market_probability_raw / reference_market_side /
    reference_market_price only — never market_prior_probability.

8B.4 Calibration — Three-Stage Ladder

    N < 200   -> Phase A: CONSERVATIVE_EMPIRICAL_BAYES_SHRINKAGE_V1
                 p_shrunk = 0.5 + lambda*(p_raw - 0.5)
                 lambda = n_eff / (n_eff + 25)
                 n_eff reflects model evidence, not raw L10 game count
                 bounds: 10th/90th percentile from >=2,000 bootstrap
                 realizations (>=10,000 preferred for offline work)
                 calibration_status = PRECALIBRATION_SHRINKAGE
                 MONEY_QUALIFIED and FINAL_APPROVED PROHIBITED in this
                 phase, regardless of confidence-lane strength

    N >= 200  -> Phase B: PLATT_TIME_SPLIT_V1
                 P(Y=1) = sigmoid(a + b*logit(p_model)), out-of-fold only
                 5-fold time-aware CV; report Brier, log loss, ECE,
                 calibration bias; no future games in training folds
                 promotion requires improvement in Brier OR log loss with
                 no material ECE/other-metric deterioration

    N >= 500 AND >= 30 obs per populated probability region
              -> Phase C: isotonic candidate, evaluated against Platt
                 promotion requires: lower Brier AND lower/non-worse
                 log loss AND lower ECE, else remain on Platt

8B.5 Host Registry — Renamed and Restructured

    host_type = PROJECT_CHAT | WOW_CUSTOM_GPT | legacy platform_BACKEND |
                EXTERNAL_GOVERNED_BACKEND

    ("FREE_HOST_BACKEND" from v1 rejected — billing tier is not an
    architectural identity.)

    Separate metadata fields (not baked into host_type):
        compute_provider   (e.g. RENDER)
        database_provider  (e.g. SUPABASE)
        batch_provider     (e.g. COLAB)
        deployment_tier    (e.g. FREE)

─────────────────────────────────────────────────
11-POINT DEPLOYMENT GATE (Gate 11 added per ChatGPT code review, 2026-08-26)
─────────────────────────────────────────────────
governed_probability_capability flips UNAVAILABLE -> AVAILABLE only when
ALL pass:

    1. Schema migration complete
    2. Regime probabilities sum to 1 (within 1e-6)
    3. No overlapping primary regimes (mutual exclusivity enforced)
    4. Deterministic simulation reproducibility (same seed -> same output)
    5. Missing-regime negative test passes (blocks publication)
    6. One-sided market cannot populate market_prior_probability
    7. Missing Goblin/Demon payout blocks money lane
    8. Prediction immutability test passes (no post-event-start edits)
    9. Calibration time-split test passes (no future leakage)
   10. Luzardo/Boyd smoke test reproduces the prior blocked diagnosis
       exactly (see Test Case below)
   11. A real end-to-end positive path: a fitted-input candidate actually
       produces a complete, valid, publishable governed probability
       (not just that failure paths correctly block — gates 1-10 could
       theoretically all pass while the scoring endpoint still returns
       nothing usable)

Until all 11 pass: Section 8A (Manual Estimate Lane) remains the only
active fallback for affected lanes.

─────────────────────────────────────────────────
TEST CASE (unchanged from v1, now the Gate 10 smoke test)
─────────────────────────────────────────────────
INPUT: Luzardo MORE 4.5 Ks, one-sided sportsbook alternate only
(Over 4.5 = -1500, no matching Under), no complete regime inputs, Goblin
payout unresolved.

REQUIRED OUTPUT:
    failure_path_status = INCOMPLETE
    independent_model_probability = NOT_PRODUCED
    calibrated_probability = NOT_PRODUCED
    probability_publishable = false
    market_prior_probability = NULL
    reference_market_price = -1500
    market_prior_weight = 0
    money_lane_status = PAYOUT_UNRESOLVED
    terminal_ceiling = RESEARCH_INTEREST

The new engine must reproduce this diagnosis exactly — not "improve" it
by inventing missing inputs.

─────────────────────────────────────────────────
DEPLOYMENT ORDER
─────────────────────────────────────────────────
[X] Step 1 — Claude drafts requirements spec (v1)
[X] Step 2 — ChatGPT ratifies methodology (v2, this document) — STATUS:
              APPROVED — ANALYTICAL METHODOLOGY / IMPLEMENTATION PENDING
[X] Step 3a — Claude implements code against ratified methodology
[X] Step 3b — ChatGPT code review: 11/11 named tests passed, but 7
              findings showed several tests didn't prove what they
              claimed (future-leakage in CV, fabricated bootstrap
              fallback, money/probability lane coupling, invalid market
              pairs accepted, unpersisted Platt coefficients / no
              isotonic implementation, meaningless Gate 3, incomplete DB
              immutability) — also recommended an 11th positive-path
              gate. STEP_3_IMPLEMENTATION: INCOMPLETE,
              governed_probability_capability remains UNAVAILABLE.
[X] Step 3c — Claude fixes all 7 findings + adds Gate 11
              (score_prop_end_to_end in engine.py). 20/20 tests now pass,
              including a real walk-forward leakage audit and a genuine
              end-to-end publishable-probability run — not just failure
              paths. Gates 1 and 8 (schema migration, DB immutability
              trigger incl. new DELETE protection) still require a live
              Supabase instance to verify — cannot be proven in this
              sandbox.
[ ] Step 3d — ChatGPT re-review of the fixed implementation
[ ] Step 4 — Greg confirms host accounts (Render/Supabase/Colab) +
              Supabase keep-alive
[ ] Step 5 — Run all 11 deployment gate items against the live host,
              including gates 1 and 8 which need real infrastructure;
              do not flip governed_probability_capability until all 11
              pass
[ ] Step 6 — Fit real per-sport cohort/regime/simulation parameters from
              actual historical data (Baseball Reference etc.) — the
              current engine correctly refuses to guess these; wiring
              them in is separate, substantive work, not a formality
[ ] Step 7 — Claude updates WOW-MASTER-SPEC.md Section 8B + host_type
              registry
[ ] Step 8 — PR/CI path once repo migration allows

═══════════════════════════════════════════════════
