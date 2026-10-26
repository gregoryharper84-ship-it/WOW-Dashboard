# WOW V17 accuracy improvement controller — shadow-only source slice

**Status:** source candidate; not a deployed model and not a verified prediction. **Class:** research/assurance infrastructure (Class A for read-only implementation; proposed future sport-model changes remain Class C).

## Why

Extend existing immutable postmortem helpers, sport-specific challengers, engineering/AMRT recovery, and the SIRT Phase-3 proposal in PR #2112. **Do not fork SIRT** or invent a new global probability authority. This slice supplies the previously missing cross-sport exact-board experimental reconciliation and paired holdout comparison. It implements the Deep Research lesson from Recursive's Eureka loop—controlled propose/test/validate feedback—without treating Flint as a sports forecasting model.

## Code and local execution

From artifacts/wow-engine:

    python -m pytest -q test_v17_accuracy_improvement_controller.py
    python -m v17.accuracy_improvement_controller --evidence-json /path/to/explicit-evidence.json

No network, API key, database, model-artifact writes, deployment or wagers. The input must include original offers (with exact labels and threshold types) and optionally a dictionary of evidence keyed by offer_id. An absent evidence entry remains NOT_SCORED. A typed backend error with its receipt stays distinct from missing evidence and from other backend errors. The output always lists **every** original market with a stable fingerprint.

## Required input contract

The top-level JSON object requires experiment_id, expected_original_offer_count, offers, and evidence_by_offer_id. Each original offer contains: offer_id, original_label, event_id, entity_id, stat, threshold_label, direction, offer_type (SCALAR, AT_LEAST, COMPOUND), sport, route, team_id, opponent_id, period, and event_start_utc. AT_LEAST must retain literal 7+, 8+ etc. COMPOUND DOUBLE_DOUBLE requires its distinct route. **No adjacent-line conversion**.

Each evidence entry requires: offer_id; identity_verification=OFFICIAL_VERIFIED; pregame_eligibility=CONFIRMED_ELIGIBLE; evidence_asof_utc strictly before kickoff. A failure may provide backend_code and backend_receipt_id. A grade requires CALIBRATION/HOLDOUT split, champion and challenger packages, and independent settlement receipt with source_id, verification_id, receipt_id, settled_at_utc, outcome (0/1), and the same offer_id, event_id, entity_id, team_id, opponent_id, period, stat, threshold_label, direction and offer_type as the exact original source market. Both model packages must be bound to the exact offer/route with prediction_id, model_artifact_id, calibrator_id, specialist_id, origin=FITTED_SPECIALIST, created_at_utc, feature_asof_utc, calibrated_probability and calibrated_lower_bound. Those strings are **attestations in an offline file**, not proof that their sources are genuine. Only a separate authenticated backend/official-adapter integration can establish live provenance.

Rows with inconsistent snapshots, duplicate receipts, fake challenger artifacts, unauthorized thresholds, or identity/eligibility failures are HELD individually. Repeated receipts and calibration/holdout event overlap are held. The calibration cohort must precede holdout chronologically.

## What metrics mean

For matched settled HOLDOUT rows only (with per-sport/route segment diagnostics and no cross-route pooled certification): paired champion and challenger Brier, log loss, 10-bin ECE, signed bias, plus seeded event-cluster bootstrap 95% Brier difference interval. At least 30 distinct holdout events are needed for even a candidate Class C review indication. A positive Brier confidence interval **does not certify** calibration, feature provenance, sample representativeness, official predictions, or model readiness; independent SIRT/QA/Release and sport-specific calibration remain required. With one-sided or insufficient input, report evidence insufficiency, never invent numbers.

## V17 invariants

- can_execute=false; automatic_promotion=false; probability_publishable=false.
- V17_TERMINAL_REDUCER remains sole global terminal authority.
- Only the controlling certified fitted specialist may supply an official governed probability.
- Keep market prices and generic LLM judgments separate from sporting probabilities.
- Never silently turn MODEL_SCORER_FAILED into MODEL_UNAVAILABLE.
- No live model registry, scorer, calibration, prediction, terminal, or GPT editor mutations.
- No self-approval, auto-promotion, or claims of real-production verification from local JSON.

## Integration plan (not yet complete)

1. Supply immutable original 27-market NBA transcription (not fabricated from the historic 8-row error account), plus canonical identity/availability checks; reconcile all 27.
2. Add read-only adapters for existing V17 model receipts and official settlement, using least privilege and evidence hashes; independently verify adapter source authenticity before claiming live results.
3. Connect to SIRT progression audit (#2112), existing postmortem ledger and AMRT typed recovery incidents. Do not duplicate code or new agents.
4. Add bounded champion/challenger training experiments (sport-specific AutoGluon/CatBoost/Bayesian distributions where justified) with frozen training/calibration/holdout splits and provenance. Optional frameworks need independently validated baselines, resource limits and Class C approvals.
5. Implement durable experiment ledger/telemetry and supervised recurring scheduling *only after* the read-only provenance and noninterference tests pass.
6. Independently verify full regression CI, release review, exact deployed SHA and authenticated read-only replay. No production activation is claimed for this source slice.
