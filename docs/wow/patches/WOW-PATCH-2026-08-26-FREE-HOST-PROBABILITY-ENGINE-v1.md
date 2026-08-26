═══════════════════════════════════════════════════
WOW PATCH — FREE-HOST PROBABILITY & CALIBRATION ENGINE
═══════════════════════════════════════════════════

PATCH ID:          WOW-PATCH-2026-08-26-FREE-HOST-PROBABILITY-ENGINE
REVISION:          v1 — draft for ChatGPT review

BASE SPEC:         WOW v16 Clean Core / Framework v2.2.0

PATCH TYPE:        [X] Analytical rule (affects pick approval logic)
                   [X] Dashboard code (new backend service replacing Replit)
                   [X] Spec amendment (host_type registry extension)
                   [ ] Memory update
                   [ ] Skill update

ORIGIN:            [X] Proactive model improvement — Replit backend gone;
                       Luzardo/Boyd postmortem (this session) diagnosed the
                       exact missing machinery: failure-path matrix,
                       calibration ledger, exact two-way no-vig, payout
                       resolution

─────────────────────────────────────────────────
PROBLEM STATEMENT
─────────────────────────────────────────────────
Per the Luzardo/Boyd postmortem, WOW's probability path requires four
components that Replit used to provide and nothing currently replaces:

1. A failure-path matrix producing P(prop) = Σ P(regime) × P(prop|regime),
   not a normal-outing assumption.
2. A calibrated probability ledger with raw_model_probability,
   independent_model_probability, market_prior_probability,
   market_prior_weight, calibrated_probability, lower_bound, upper_bound,
   calibration_method, calibration_version, source_snapshot_id.
3. Exact two-way no-vig market verification (both sides of the same
   settlement-matched line — one-sided alternate pricing is evidence, not
   verified edge).
4. Payout/EV resolution for Goblin/Demon-adjusted slips before any EV
   claim is made.

This patch specifies the **requirements and schema** for a free-hosted
replacement. It does NOT specify the actual statistical methodology
(regime weighting, calibration technique, market-prior blend weight) —
see "METHODOLOGY DECISIONS REQUIRED" below. Claude drafting that
methodology unilaterally would repeat, at much larger scale, the
unauthorized-scoring-shortcut problem already flagged and corrected
earlier in this session.

FAILURE TAG(S):    missing-projection-support, no-vig-layer-skipped,
                   payout-friction-underestimated, model-score-overtrusted

─────────────────────────────────────────────────
RULE CHANGE
─────────────────────────────────────────────────
AFFECTED SECTION:  WOW-MASTER-SPEC.md Section 8 (Layered Architecture) —
                   proposes Section 8B (Free-Host Probability Engine
                   Specification), plus a host_type registry amendment.

CURRENT RULE:
Section 8.5 (Layer 4 Synthesis) and the existing calibration-ledger rules
assume a backend exists that produces these fields. No rule currently
specifies HOW to produce them without Replit.

NEW RULE (REQUIREMENTS SPEC — methodology fields marked [DECISION
REQUIRED] are placeholders, not implemented logic):

8B.1 Failure-Path Matrix — Required Structure

Every prop requiring workload-dependent probability (pitcher K/outs
props, usage-dependent player props, etc.) must produce a regime
distribution before any unconditional probability is published.

    regime_set: [DECISION REQUIRED — ChatGPT to confirm or amend the
                 candidate taxonomy below, which is not yet ratified]

      NORMAL_EFFECTIVE_OUTING
      INEFFICIENT_SURVIVING_OUTING
      EARLY_PERFORMANCE_HOOK
      COMMAND_COLLAPSE
      HEALTH_OR_WORKLOAD_RESTRICTION
      ENVIRONMENTAL_DISRUPTION
      OPPONENT_EXTENSION

    Per regime, required fields:
      regime_probability          [DECISION REQUIRED: estimation method —
                                    e.g. logistic model on leash/workload
                                    history, rule-based heuristic scoring,
                                    or Bayesian prior + update — ChatGPT
                                    to specify]
      expected_innings / expected_PA / expected_pitch_count
      stat_rate_distribution_in_regime
      P(prop | regime)

    Output: P(prop) = Σ [P(regime) × P(prop | regime)]

    No regime may be silently treated as zero-probability. Missing regime
    data blocks unconditional probability publication — this rule is
    already implicit in existing WOW data-integrity rules and is not new.

8B.2 Calibrated Probability Ledger — Required Schema

    prediction_id            (immutable once written)
    created_at
    event_id / event_start_time
    player / team / opponent / sport
    market_type / stat_type / line / direction

    raw_model_probability          — pre-calibration model output
    independent_model_probability  — WOW's own model, isolated from market
    market_prior_probability       — from exact two-way no-vig (8B.3)
    market_prior_weight            [DECISION REQUIRED: how much market
                                     prior influences calibrated_probability
                                     — e.g. fixed weight, confidence-based
                                     weight, or Bayesian shrinkage —
                                     ChatGPT to specify]
    calibrated_probability
    calibrated_probability_lower_bound
    calibrated_probability_upper_bound
    calibration_method              [DECISION REQUIRED: e.g. isotonic
                                     regression against historical settled
                                     outcomes, Platt scaling, or a simpler
                                     rule-based bound-widening approach
                                     given limited settled-sample size at
                                     restart — ChatGPT to specify, noting
                                     current sample size may be too small
                                     for a data-hungry method]
    calibration_version
    source_snapshot_id
    failure_path_score
    normal_workload_probability

    Constraint: lower_bound <= calibrated_probability <= upper_bound.
    Missing required field => calibrated_probability_status = NOT_PRODUCED,
    same as existing rule — no bet reaches MODEL_QUALIFIED_HOLD or above
    without a complete row.

8B.3 Exact Two-Way No-Vig Verification

No new rule — restates existing WOW Exact Line, Payout, Push & Two-Way
No-Vig Edge Auditor requirement explicitly inside this engine's pipeline:
both sides of the same settlement-matched line are required before
market_prior_probability may be populated. A single alternate-line price
(e.g. Over 4.5 = -1500 alone) populates a disclosed reference field only,
never market_prior_probability.

8B.4 Payout/EV Resolution

No new rule — restates existing PrizePicks EV rule explicitly inside this
engine's pipeline: Goblin/Demon-adjusted slips require the actual
payout/multiplier and slip structure before EV = p × payout − loss
probability can be calculated. Until resolved, money_lane_status =
PAYOUT_UNRESOLVED and the candidate cannot exceed RESEARCH_INTEREST
regardless of confidence-lane strength.

8B.5 Host Registry Amendment [DECISION REQUIRED]

Proposed addition to host_type enumeration:
    host_type = PROJECT_CHAT | WOW_CUSTOM_GPT | REPLIT_BACKEND |
                FREE_HOST_BACKEND

FREE_HOST_BACKEND candidate implementation (pending ChatGPT/Greg decision,
not yet built):
    compute:  Render free web service (FastAPI) — accept ~30-60s cold
              start on first call per session; verify this is within any
              custom-GPT tool-call timeout before relying on it live
    ledger:   Supabase free Postgres — requires a scheduled keep-alive
              (e.g. GitHub Actions cron) to avoid the 7-day inactivity
              pause; without one, this is not a dependable governed
              backend and should not be represented as always-available
    batch:    Google Colab — NOT an API-callable component. Manual/batch
              use only (periodic calibration recomputation, Monte Carlo
              regime modeling), results pushed to Supabase. Cannot be
              positioned as something ChatGPT invokes automatically.

    replit_is_model_layer = false  (already established) →
    free_host_is_model_layer = true only once 8B.1–8B.4 are implemented
    and this patch is fully ratified; until then,
    governed_probability_capability = UNAVAILABLE per Section 8A.1, and
    Section 8A (Manual Estimate Lane) remains the correct fallback.

─────────────────────────────────────────────────
METHODOLOGY DECISIONS REQUIRED (ChatGPT — not Claude — to specify)
─────────────────────────────────────────────────
1. Regime taxonomy: confirm, amend, or replace the 7-regime candidate
   list in 8B.1.
2. Regime probability estimation method (rule-based heuristic vs.
   statistical model vs. Bayesian).
3. market_prior_weight blending rule for 8B.2.
4. calibration_method for 8B.2 — given a fresh ledger will have a small
   settled-sample size at first, a data-hungry method (isotonic
   regression) may not be appropriate initially; a simpler
   uncertainty-widening rule may be needed until enough settled rows
   accumulate. ChatGPT to specify both the initial method and the
   sample-size threshold for switching to a data-driven method.

Claude will not implement 8B.1–8B.2 in executable code until these four
items are specified or explicitly delegated back to Claude with
constraints. This mirrors the no-self-certification rule already active
for Section 8A (8A.4) and the earlier retracted unauthorized-scoring
incident this session.

─────────────────────────────────────────────────
IMPLEMENTATION
─────────────────────────────────────────────────
ANALYTICAL IMPACT:
Once methodology is specified, this becomes WOW's replacement governed
scoring path. Until then, no candidate may be scored through this engine
— Section 8A Manual Estimate Lane remains the only active fallback for
lanes with no governed capability.

DASHBOARD IMPACT:  [X] Yes — new backend service, host TBD (8B.5)
IF DASHBOARD: FUNCTION TO MODIFY:
New service — not a modification of existing Replit code (Replit is gone).
Claude (chat or Claude Code) implements once 8B methodology is ratified.

CODE CHANGE:
Not yet written. Scope once ratified: FastAPI service on Render exposing
/health, /governance, /score-prop, /failure-path, /calibrate, /prediction,
/settle, /calibration-report; Supabase schema per 8B.2;
Python failure-path/regime module per 8B.1.

─────────────────────────────────────────────────
TEST CASE
─────────────────────────────────────────────────
INPUT:
Luzardo 4.5 K Over, Goblin-adjusted line, one-sided alternate price only
(Over 4.5 = -1500, no matching Under 4.5 price), no failure-path regime
data available yet.

EXPECTED OUTPUT (once engine is built per this spec):
CONFIDENCE: failure_path_status = INCOMPLETE (regime data missing) →
  calibrated_probability_status = NOT_PRODUCED
MARKET: market_prior_probability = NOT_POPULATED (one-sided only) →
  disclosed reference price only
MONEY: money_lane_status = PAYOUT_UNRESOLVED (Goblin payout table missing)
SLIP: not eligible above RESEARCH_INTEREST
This matches the actual scoped diagnosis already reached in this
session's postmortem — the engine should reproduce that diagnosis
mechanically, not override it.

NEGATIVE TEST (should NOT trigger):
Same prop, but engine outputs a calibrated_probability despite an
incomplete regime distribution, or populates market_prior_probability
from the one-sided alternate price alone — both are patch violations.

─────────────────────────────────────────────────
CONFLICTS / DEPENDENCIES
─────────────────────────────────────────────────
CONFLICTS WITH:    None identified — restates and implements existing
                   calibration, no-vig, and payout rules rather than
                   changing them.
DEPENDS ON:        Section 8A (Manual Estimate Lane) remains active and
                   correct for any lane where this engine is not yet
                   built or not yet ratified; WOW Exact Line, Payout,
                   Push & Two-Way No-Vig Edge Auditor (8B.3); existing
                   PrizePicks EV/payout rule (8B.4); the four methodology
                   decisions above must be resolved before code ships.
SUPERSEDES:        None — this is additive infrastructure, not a rule
                   change to existing gates.

─────────────────────────────────────────────────
DEPLOYMENT ORDER
─────────────────────────────────────────────────
[X] Step 1 — Claude drafts requirements spec (this document)
[ ] Step 2 — ChatGPT reviews requirements spec + resolves the four
              methodology decisions (or explicitly delegates constraints
              back to Claude)
[ ] Step 3 — Greg confirms host choice (Render+Supabase per prior
              analysis, or alternative) and sets up accounts/keep-alive
[ ] Step 4 — Claude/Claude Code implements 8B.1–8B.4 as code against
              ratified methodology
[ ] Step 5 — Claude updates WOW-MASTER-SPEC.md Section 8B +
              host_type registry
[ ] Step 6 — Smoke test against the Luzardo/Boyd test case above —
              engine must reproduce the same scoped diagnosis
[ ] Step 7 — PR/CI path once repo migration allows (same sequencing
              caveat as the Manual Estimate Lane handoff)

STATUS:            [X] Proposed
                   [ ] Approved — analytical only
                   [ ] Approved — pending dashboard build
                   [ ] Deployed
                   [ ] Rejected — reason: ___

═══════════════════════════════════════════════════
