# MLB Pitcher Strikeouts — Production Status (V17)

Authority: this document is evidence/status only. It does not certify,
promote, or change any probability, calibration, or capability. See
`CLAUDE.md` for the governing authority order. `can_execute=false`.

Scope note: this document covers **PITCHER_STRIKEOUTS** only. It is a
different prop than MLB 1IP (first-inning/one-inning-pitched outcomes); the
existing `MLB_1IP_*` status documents in this directory do not cover
strikeouts and should not be read as if they did (confirmed by grep: zero
mentions of "strikeout" across `MLB_1IP_PRODUCTION_CAPABILITY_STATUS.md`,
`MLB_1IP_EMPIRICAL_MODEL_DECISION.md`, `MLB_1IP_FULL_MODEL_GOVERNED_STATUS.md`).

## Status as of 2026-09-19

| # | Component | Status | Evidence |
|---|---|---|---|
| 1 | Fitted artifact + training/calibration report | SHIPPED | `data/wow_mlb_pitcher_strikeouts_artifact_v1.json` (real fitted NB constants, `training_rows: 4489`, out-of-sample `validation_metrics` beating baseline Brier/NLL); `data/wow_mlb_pitcher_strikeouts_training_report_v1.json` |
| 2 | Feature/data-ingestion pipeline | SHIPPED | `prop_auto_hydration.py` (`_game_log`, `_hydrate_opponent_context`, `auto_hydrate_prop_evidence`) pulls K-rate/box-score history and opponent hand-split K-rate live; enforces `MIN_STARTS`, fails closed with `MLB_RECENT_STARTS_INSUFFICIENT` rather than fabricating |
| 3 | Leakage-safe as-of snapshots | SHIPPED | `prop_auto_hydration.py` filters prior starts to strictly before `event_start`; rejects post-start hydration with `EVENT_ALREADY_STARTED`; feature snapshot is hashed into the returned distribution |
| 4 | One controlling specialist | SHIPPED | `v17/prop_capability_manifest.py` declares `MLB_STRIKEOUT_EXPERT` (`wow.mlb-pitcher-failure-path-expert`) as the sole certified specialist for `("MLB", "PITCHER_STRIKEOUTS")`; `prop_model_adapters.py` registers exactly one adapter, `mlb_pitcher_so_failure_path_nb_v1_adapter` |
| 5 | Failure taxonomy (MODEL_UNAVAILABLE / MODEL_SCORER_FAILED / MODEL_INPUTS_INSUFFICIENT / MODEL_OUTPUT_INVALID) | **VERIFIED SHIPPED** | Independently re-verified for this prop in `v17/test_mlb_strikeout_failure_taxonomy_verification.py` (10/10 passing). Confirms: the four canonical blocker sets in `prop_terminal_reducer_v2.py` are pairwise disjoint (no category can collapse into another); the strikeout-specific `MLB_RECENT_STARTS_INSUFFICIENT` blocker lands in exactly `MODEL_INPUTS_INSUFFICIENT`; the strikeout adapter's `PROP_MODEL_ARTIFACT_PAYLOAD_INVALID` contract error is classified as an output/contract failure (`_OUTPUT_INVALID_CODES` in `calibration_publication_api.py`) and never as capability absence. Two classification surfaces exist by design, not by accident: `prop_terminal_reducer_v2.reduce_prop_terminal` reduces a row that reached (or was deliberately not sent to) model evaluation; `api_prod_market._raise_model_path_error` surfaces a raw adapter/provider exception code as an HTTP error *before* a row exists to reduce. Neither path was found to relabel a scorer, input, or output failure as `MODEL_UNAVAILABLE` for this prop. |
| 6 | Calibration lower bound / terminal reducer | SHIPPED | `prop_calibration_adapters.py` (`_mlb_pitcher_so_resample_fn`) — dedicated bootstrap resampler feeding `phase_a_shrinkage`, producing `calibrated_probability`/bounds specifically for this model family |
| 7 | Acceptance/regression tests passing | SHIPPED (locally verified, this session) | `python3 -m pytest v17/test_mlb_strikeout_calibration_health.py v17/test_mlb_strikeout_failure_taxonomy_verification.py test_prop_model_adapters.py test_prop_calibration_adapters.py test_prop_auto_hydration_opponent_context.py test_prop_real_artifact_e2e.py -q` → 54 passed, after installing `fastapi`/`celery`/`pytest` (missing in the bare sandbox — `CI_ENVIRONMENT_GAP`, not a code defect). A repo-wide `-k strikeout` collection still hits `pyo3_runtime.PanicException` in ~85 unrelated files (Rust-extension ABI mismatch, reproduced on files neither this session nor the Gap #9 PR touches) — this remains open as a separate environment task (see Gap #7 below), not evidence against strikeouts specifically. |
| 8 | Immutable prediction/outcome logging | SHIPPED | `v17/prop_exact_route_settlement.py` explicitly includes `PITCHER_STRIKEOUTS` in its certified settlement route list; `v17/prop_universal_forward_evidence.py` has a dedicated `COLLECTOR_STRIKEOUT` collector |
| 9 | Calibration/drift monitoring | **SHIPPED (this session)** | `v17/mlb_strikeout_calibration_health.py` — read-only rolling health monitor (Brier, log loss, observed-vs-predicted hit rate, reliability error, calibrated-lower-bound coverage, feature drift) over immutable settled predictions; emits `HEALTHY` / `WATCH` / `INSUFFICIENT_SAMPLE`; cannot mutate stored probabilities or retrain/recalibrate/disable the specialist. See PR: MLB pitcher-strikeouts calibration/drift health monitor (V17 Gap #9). |

## Remaining open items

- **Gap #7 (environment)**: resolve the `pyo3_runtime.PanicException` collection failures affecting ~85 unrelated test files in a dependency-complete CI environment, then run and record the full `-k strikeout` acceptance/regression suite result. Not attempted as code changes in this document's session — it is an infrastructure task, not a probability/calibration change, and is explicitly out of scope for this status/verification pass.
- This document should be updated whenever the strikeouts vertical's fitted artifact, adapter, calibration, or monitoring wiring materially changes.
