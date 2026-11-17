# WOW V17 — Opponent-Aware Forward-Shadow Handoff

**Class C research only.** No scoring-route, web-service, worker, database, existing model, existing gate, or execution changes. `CAN_EXECUTE=False`, `AUTOMATIC_PROMOTION=False`, `V17_TERMINAL_REDUCER` remains sole terminal authority.

## Why this build

As of the November 17, 2026 validation data read, WOW **already has** a fitted NFL dynamic team-state logistic challenger. The latest artifact `NFL_DYNAMIC_TEAM_STATE_LOGIT_V2_092582a3ed720945_ed8a8a1c9f71` was trained on 892 events, calibrated on 298 and tested on 298; it improves Brier/log loss against the prevalence baseline, but its calibrated ECE was 0.11076370003951416 versus the predeclared 0.10 threshold. `research_screen_pass=false`, `source_review_status=REQUIRED`, `lifecycle_state=CANDIDATE`. Do **not** relax the threshold. Do **not** treat this as an eligible Full Model probability.

The existing fitted trainer is `v17/team_state_challenger_training.py` using pre-event opponent/team states from `v17/team_state_intelligence.py`; the existing research diagnostics and slip exposure logic are in `v17/experiments/nov16_ml_slip_learning`. This package is a **thin, source-validated research adapter** around the existing candidate, not a new unfitted model. The extra checks distinguish an artifact's actual creation time from its historical training cutoff: postgame artifacts must never be backdated as pregame forecasts.

## Components

- `opponent_challenger_bridge.score_existing_team_state_candidate`: requires the exact existing fitted JSON artifact, artifact checksum, independently retained calibrator checksum, feature order, completed pregame feature vector, cutoff timestamps and source IDs. Reproduces its standardized logistic and selected identity/nearest-bin empirical calibrator. Preserves candidate-only status and **does not mint lower bounds**.
- `bridge_team_script_to_prop`: joins LLP team-event fitted leading/tied/trailing state probabilities with separately fitted WOW_PROP_LANE conditional count distributions. Validates sport, event, team, player, source, cutoff and state coverage. No implied-moneyline proxy; no hypothetical Poisson tails; no betting execution.
- `build_paired_forward_row`: joins a frozen incumbent, separately frozen research candidate and independently settled result with exact event, selected side, market, settlement and chronology. Supports home/away conversion, produces paired Brier contributions; never certifies a model.
- `offline_forward_shadow.py`: read-only local JSON CLI. Outputs JSON with candidate status and provenance limitations. Exits with code 2 when any candidate is unavailable.

## Run

```bash
python -m pytest -q artifacts/wow-engine/v17/experiments/nov17_opponent_challenger_bridge/test_opponent_challenger_bridge.py
python artifacts/wow-engine/v17/experiments/nov17_opponent_challenger_bridge/offline_forward_shadow.py \
  /path/to/verified_frozen_artifact_and_feature_receipts.json --output /tmp/wow-forward-shadow.json
```

Input format: `{"artifact": {...}, "features": [{...}]}`; see the tests for minimal synthetic fixtures. `artifact_payload` is the **existing** `STANDARDIZED_LOGISTIC_JSON_V1` with complete `feature_names`, `scaler_mean`, `scaler_scale`, `intercept`, `coefficients`, and `calibrator_payload` from the existing candidate. The manifest must carry a separately retained `calibrator_content_hash` (SHA-256 over canonical calibrator JSON), a verified creation time, fit cutoff, calibration cutoff, created-before-snapshot evidence, and schema/feature content SHA-256. Claimed hashes and source IDs are not independently authenticated by this module; real source review must verify them before publishing results.

## Superpowers build process — PLAN → TDD → VERIFY → REVIEW

1. **PRESERVE**: incumbent and existing fitted challenger, the 0.10 calibration screen, stage-20 immutable receipts, source rights, existing per-card exposure gates, current full-slate winner policy, and read-only execution.
2. **RED**: run focused tests before implementation; the module-import test failed as expected.
3. **GREEN**: implement the exact scorer and score-state bridge, then test incorrect hashes, future timestamps, wrong-side conversions, missing states and disputed publication flags.
4. **REFINE**: collect authentic immutable future pregame candidate and incumbent receipts on matching events, settle from independent official results, and use existing `team_state_challenger_training.compare_shadow_predictions` for paired Brier/log loss. Use forward samples plus predeclared offline replays; **do not** claim existing 107 NFL incumbent grades are paired with this newly created adapter.
5. **REGRESSION_CHECK**: independent source validation, statistical calibration including uncertainty by probability band, unchanged incumbent wins and scenario sensitivity, exact-head CI, GitHub protection policy, then separate scientific approval before model promotion.

## Exit / status

`RESEARCH_SCREEN_FAILED` is the expected status for the current NFL candidate, even if a research probability is computable. `SHADOW_CANDIDATE_ONLY` does not imply promotion; lower bound remains null. Real forward-shadow coverage is **not established** by synthetic tests. No executable values, actual multi-leg slips or online model predictions are issued by this package.