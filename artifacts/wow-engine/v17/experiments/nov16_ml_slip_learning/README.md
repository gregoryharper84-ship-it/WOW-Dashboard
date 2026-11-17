# WOW V17 — November 16 lessons: offline implementation

**Status:** `CLASS_C_RESEARCH_ONLY / SYNTHETIC_TESTS_GREEN / REAL_HOLDOUT_PENDING`.
This additive package is intentionally separate from serving, scoring, existing probability models and any release path. `can_execute=false`, `automatic_promotion=false`, `V17_TERMINAL_REDUCER` remains the sole terminal authority. It does **not** place wagers, submit entries, calculate certified win probabilities, or make production changes.

## Scope and ownership

Connect only through the existing downstream read-only interfaces:

- Project Hub [#2076](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/issues/2076) owns the Pick'em downstream policy. **LLP_TEAM_BETTING_ENGINE** owns team-event moneyline, upset and score-state scenario distributions. **WOW_PROP_LANE** owns scalar count distributions, not an inferred derivative of moneyline probability.
- Existing `v17/experiments/nov14_followon_intelligence/followon.py` implements the **dual probability/economics gates** and the **same-event cross-market audit**. This module does not duplicate or weaken those gates.
- Existing `v17/slip_portfolio_optimizer.py` and `v17/portfolio_exposure_gate.py` retain structural enforcement and per-card/session thesis identity. `slip_exposure_shadow` adds only a nonpublishing **cross-slip comparison** for the six hypothetical Nov. 16 structures. Its soft heuristic ranking penalty does not estimate sporting win probability or bypass other restrictions.
- Existing `v17/llp_postmortem_recalibration.py` defines `lower_bound_empirical_gap = observed_win_rate - mean_lower_bound`; this package uses the same sign.
- Existing 13-sport schedule/fatigue, odds normalization and official-result readers remain authoritative. This experiment consumes their *frozen, externally verified* inputs; it is not a new scraper or sport-specific fitted model.

## Six pure research functions

1. `score_prop_projection`: line-specific hit/push; exact continuous projection MAE contribution and signed error; **discrete distance above the winning line**; optional full discrete-PMF CRPS. McDaniels 9 vs 8.5 and Miller 2 vs 1.5 are both `near_threshold=True`, **not** evidence that predicted medians were near lines. Missing/invalid PMF never becomes a made-up distribution score.
2. `conditional_usage_shadow`: consumes separately fitted score-state probabilities (LEADING/TIED/TRAILING) and state-conditional prop count mean, variance, exact-line tail probabilities; outputs mixture expectation/variance/P(over). **Never derives these from favorite ML price alone**, never recalibrates an actual model, validates event/time/fit provenance. For Henry rushing volume, the trailing state must be allowed to predict fewer carries.
3. `revision_regret_shadow`: preserves two caller-supplied *distinct immutable receipt IDs* with timestamps and new-evidence provenance. Grades the two selected candidates with independently settled outcomes and produces Brier regret. Only same exact proposition, event, line and market receives a lower-bound delta; **different player/market replacement cannot be represented as recalibration of one target**. No auto-lock from a single slate. This pure function does not itself persist immutable receipts; persistence must occur in the existing ledger.
4. `slip_exposure_shadow`: groups distinct slip IDs by canonical `(sport, official_event_id)` and user-supplied shared thesis; reports duplicated event use, optional >50% concentration warning, and an explicit soft **research rank** penalty. It never invents `same_event_cross_market_correlation` or joint success probability; both remain `None` until an independently validated joint distribution exists. Does not enforce a universal exposure ceiling.
5. `chronological_holdout_shadow`: paired incumbent/challenger Brier/log loss, signed lower-bound empirical gap and optional prop projection MAE. Requires exact event and proposition identity, explicit TRAIN→CALIBRATION→UNTOUCHED HOLDOUT dates, frozen pre-event prediction, pre-cutoff model and calibrator hashes, and independent official-final attribution; any row failing checks invalidates the whole batch. It does not train, certify or promote a model.

6. `joint_scenario_slip_shadow`: estimates a complete combination's success probability only from a supplied **frozen weighted joint-scenario distribution** that simultaneously resolves every exact leg. A marginal-product shortcut is forbidden. An optional supplied exact combined offer can produce a **research-only, unauthenticated** break-even and edge diagnostic; no live executable price or calibrated joint probability is claimed. Same-event cross-market authority remains with the existing audit.

**Important trust boundary:** Receipt IDs, hashes and official-source strings in the input are claimed metadata. A passing unit test does not authenticate their external issuance. Scientific certification requires independently fetching/verifying historical receipts and official results, comparing calibration by sport/market/price band, accounting for uncertainty and sample size, and independent nonauthor acceptance at the exact code SHA.

## Local usage

At repo root after applying patch:

```bash
python -m pytest -q artifacts/wow-engine/v17/experiments/nov16_ml_slip_learning/test_learning_shadow.py
python artifacts/wow-engine/v17/experiments/nov16_ml_slip_learning/offline_holdout_validate.py \
    /path/to/independently_verified_frozen_holdout.jsonl \
    --train-end 2026-09-30T00:00:00Z \
    --calibration-end 2026-10-31T00:00:00Z \
    --holdout-start 2026-11-01T00:00:00Z \
    --holdout-end 2026-11-30T00:00:00Z \
    --out /tmp/wow-nov16-holdout-report.json
```

The sample split dates are **illustrative only**; use real, prespecified untouched splits for any scientific comparison. Do **not** treat synthetic fixtures as real holdout evidence. The CLI rejects rows marked `synthetic=true` and fails on malformed rows.

Each holdout JSONL row must contain:

- `sport`, `official_event_id`, `proposition_id`, `exact_line`, `event_start_utc`;
- `forecast_at_utc`, `model_fit_cutoff_utc`, `calibration_fit_cutoff_utc`, `immutable_forecast_receipt_id`, `source_snapshot_hash`, `baseline_model_hash`, `challenger_model_hash`, `calibration_receipt_id`;
- `finalized_at_utc`, `settlement_source_type="INDEPENDENT_OFFICIAL"`, `settlement_source_id`, `outcome` (boolean for **the identical proposition**);
- `baseline_probability`, `challenger_probability`, `baseline_lower_bound`, `challenger_lower_bound` in [0,1] with each lower bound <= its respective probability;
- optional integer `actual_count` and **both** `baseline_projected_mean` and `challenger_projected_mean` for MAE.

Separate datasets are needed by market type and sport; a player-prop over at 5.5 is **not** the same proposition as over at 7.5, and NHL regulation-only versus full-game including OT is not interchangeable. Baseline and challenger must have the same outcome target, cutoff and eligible evidence, not newly re-created postgame probabilities.

## Regression coverage

`test_learning_shadow.py`: synthetic tests for near-threshold won-by-one counts and missing projections, count-PMF CRPS, exact-line mismatch, pushes, invalid/stale timestamps, foreign event receipt, tied/leading/trailing mixture and Henry score-state monotonicity, immutable revision evidence, original-vs-revised loss, no auto-lock, Utah repeated across A/B/F, same raw event ID across different sports, cross-event narrative exposure, concentration warnings, no fabricated joint P, chronology and fit/calibration leakage, duplicate holdout identity, numeric NaNs, no probability mutation, and V17 execution/terminal invariants.

## Acceptance sequence

1. **Code/tests:** unit tests green in this sandbox; repository CI and exact-head independent code review not yet run.
2. **Authentic replay:** retrieve independent Stage-20 or equivalent immutable forecasts, model hashes and official settlement. If receipts cannot be found, report `DATA_NOT_AVAILABLE`; never synthesize a fitted backtest.
3. **Paired scientific comparison:** same-date, same-event, same-line, matched baseline versus challenger with Brier/log loss, lower-bound gap, count CRPS/MAE, and confidence intervals. Preserve incumbent wins in Memphis/Philadelphia and assess Boston, Denver, Utah, Baltimore scenarios without postgame features.
4. **Promotion:** only after independent scientific acceptance and protected release workflow, with `V17_TERMINAL_REDUCER` sole terminal authority and `can_execute=false`. This package itself has no promotion route.
