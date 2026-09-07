# WOW V17 Kalshi Weather — Implementation Status

**Date:** 2026-09-07  
**Branch:** `feat/v17-kalshi-weather-intelligence`  
**PR:** #246  
**Runtime target:** V17_ACTIVE  
**Execution:** `can_execute=false`

## Implemented in repository

- V17 architecture and implementation contract.
- Station-specific multi-model final-high distribution engine.
- Integer-temperature PMF with exact bracket/threshold projection.
- Forecast-family dependence control.
- Station/model historical error-profile learner.
- Intraday official-observation likelihood reweighting.
- Hard `P(final_high < official_max_so_far)=0` support truncation.
- Numerical weather-regime mixtures.
- Explicit Gaussian fallback labeling.
- Dynamic calibration contract; no fabricated lower bound.
- Immutable/tamper-evident forecast and observation snapshots.
- Versioned verified settlement-station registry for current production series.
- Known-station regression bans (KORD/KPBI/KBUR substitutions).
- Calibration health metrics: Brier, log loss, bias, ECE, lower-bound reliability.
- Offline isotonic calibration challenger fitter.
- Offline historical replay harness.
- Append-only Postgres prediction ledger plus separate immutable outcome ledger.
- Legacy downstream compatibility adapter that does not mutate model probability.
- Weather gate separation: model PMF normalization is distinct from Kalshi price coherence.
- Explicit `LEGACY_RESEARCH_ONLY`, `V17_RESEARCH_ONLY`, and `V17_GOVERNED` probability-governance status.
- Regression coverage for station identity, probability normalization, market leakage, calibration bounds, intraday support, registry, ledger, replay, and downstream mutation.

## P0 fixed in code semantics

The legacy weather gate previously described the sum of Kalshi YES prices as `probability_normalization`. V17 now treats:

- weather PMF normalization as **model evidence**; and
- Kalshi YES-price coherence as **market evidence**.

A legacy row may remain discoverable without becoming governed model probability.

## P0 still requiring route integration

The monolithic production Flask category-scan path currently contains a legacy heuristic assignment similar to:

`calibrated_prob_lower_bound = 0.70 if WEATHER_MODEL_READY else 0.50`

That heuristic MUST NOT be interpreted as a governed V17 lower bound. Production V17 weather publication must instead consume `weather_v17_probability_package.calibrated_lower_bound` produced from certified calibration evidence.

Because the production route is a large monolithic `app.py`, this branch deliberately does not claim route-level completion until that wiring is applied and regression-tested. Discovery may continue under explicit legacy/research-only status.

## Certification still required

Repository implementation is not equivalent to production model certification. Before `MODEL_QUALIFIED` weather publication is enabled end-to-end, require:

1. Required GitHub CI green.
2. Real historical replay using frozen forecast snapshots and official settled daily highs.
3. Station × model × horizon error-profile population from real data.
4. Calibration sample and lower/upper-bound evidence sufficient for the publication cohort.
5. Baseline Gaussian versus V17 challenger comparison (Brier, log loss, ECE, final-high MAE, lower-bound reliability).
6. Real intraday replay showing official observations change the posterior and never violate max-so-far support.
7. Route wiring: `/wow/kalshi/weather/evaluate` / category scan must consume the V17 probability package before any governed probability label.
8. Immutable pre-settlement prediction write plus official post-settlement outcome write.
9. Exact fresh Kalshi price/fee/edge handoff only after completed weather probability.
10. Portfolio governor and final-refresh handoff without probability mutation.
11. Render deployment verification.
12. Live endpoint replay and typed failure verification.
13. V17_TERMINAL_REDUCER verification.
14. LIVE_GPT_EDITOR_SYNC only after backend/action schema is saved and verified.

## State summary

- `REPOSITORY_IMPLEMENTATION`: SUBSTANTIAL / PR_OPEN
- `MODEL_CAPABILITY`: V17 core implemented; certified calibration data not yet proven
- `BACKEND_RUNTIME`: existing V17 runtime remains active; this PR is not yet deployed
- `REPOSITORY_GOVERNANCE`: protected main / required checks apply
- `LIVE_GPT_EDITOR_SYNC`: unchanged by this branch
- `can_execute`: false
- `DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS`: true
