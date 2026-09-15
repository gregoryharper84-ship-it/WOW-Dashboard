# NFL-CERTIFICATION-AUDIT-2026-09-15

Repository audit requested by
`CLAUDE-CODE-FOLLOWUP-2026-09-14-CERTIFICATION-AND-DISCOVERY-MAP`, answering the
G-NFL-01..G-NFL-18 evidence questions from
`WOW-PATCH-2026-09-14-NFL-CERTIFICATION-AND-AUTHORITATIVE-DISCOVERY-MAP`.

This is an evidence inventory, not a certification decision. It records what the
repository actually contains, established by reading the code rather than the
documentation.

```text
certification_recommendation = DO_NOT_CERTIFY_YET
current_state                = CANDIDATE_REGISTERED_UNCERTIFIED
can_execute                  = false
```

## 1. Evidence present

| Item | Value | Source |
|---|---|---|
| Fitted provider identity | `WOW_NFL_EVENT_FITTED_MODEL_V1` | `nfl_event_model_v17.PROVIDER_IDENTITY` |
| Model family | `NFL_OUTRIGHT_WIN_LOGREG_V1` | `nfl_event_model_v17.MODEL_FAMILY` |
| Artifact format | `JSON_COEFFICIENT_BUNDLE_V1` | `nfl_event_model_v17.ARTIFACT_FORMAT` |
| Model artifact version | `NFL_EVENT_LOGREG_PLATT_V1_<dataset_hash[:12]>` | `fit_candidate` |
| Artifact checksum | SHA-256 over the coefficient payload | `fit_candidate` |
| Bundle fingerprint | SHA-256 over checksum + dataset hash + feature-order hash + calibration | `fit_candidate` |
| Feature schema version | `FEATURE_SCHEMA_VERSION` with pinned `FEATURE_ORDER` and order hash | `nfl_event_model_v17` |
| Feature transform version | `STANDARD_SCALER_V1` | `nfl_event_model_v17` |
| Calibration method | `PLATT_TIME_SPLIT_V1` | `nfl_event_model_v17.CALIBRATION_METHOD` |
| Calibration version | `NFL_PLATT_TIME_SPLIT_V1_<dataset_hash[:12]>` | `fit_candidate` |
| Bounds method | `CALIBRATION_HOLDOUT_ERROR_MARGIN_V1` | `nfl_event_model_v17.BOUNDS_METHOD_VERSION` |
| Temporal split | train 2021–2023, calibration 2024, validation 2025 | `nfl_event_model_v17` |
| Minimum cohort sizes | train ≥ 750, calibration ≥ 250, validation ≥ 250 | `MIN_TRAIN_N` / `MIN_CALIBRATION_N` / `MIN_VALIDATION_N` |
| Required-input contract | `TEAM_EVENT_INPUT_CONTRACTS["NFL"]` (8 fields) | `v17/team_event_capability_manifest.py` |
| Governed input adapter | `resolve_nfl_team_event_evidence` over the canonical NFLverse ledger | `v17/nfl_team_event_specialist.py` |
| Scorer entry point | `score_nfl_team_event` | `v17/nfl_team_event_specialist.py` |
| Publication/governance chain | `score_nfl_team_event_request` → `wow_v17_nfl_team_event_governance_bridge` | `v17/nfl_team_event_publication.py` |
| Failure taxonomy | `NFLModelUnavailable` / `InputsInsufficient` / `ScorerFailed` / `OutputInvalid` | `nfl_event_model_v17` |
| Prediction ledger | `wow_nfl_event_predictions` | `v17/nfl_team_event_specialist.py` |
| Artifact registry | `wow_nfl_event_fitted_model_artifacts` | `nfl_event_model_v17` |
| Supported regimes | `REGULAR_SEASON` only (declared on the bridge registration) | `v17/team_event_bridge_runtime.py` |

### Self-certification gates the fit already enforces

`fit_candidate` refuses to mark a candidate certified unless all of:

```text
train_n                 >= 750
calibration_n           >= 250
validation_n            >= 250
beats_baseline_brier    validation Brier  <  calibration-base-rate Brier
beats_baseline_log_loss validation logloss < calibration-base-rate logloss
auc_not_below_chance    AUC >= 0.50
ece_within_limit        ECE(10 bins) <= 0.12
platt_positive_slope    Platt slope > 0
```

These are retrospective holdout gates. They are a precondition for certification,
not certification itself.

## 2. Gate-by-gate status

| Gate | Status | Note |
|---|---|---|
| G-NFL-01 model identity | PRESENT | provider + family pinned |
| G-NFL-02 artifact/version pinned | PRESENT | version, checksum, bundle fingerprint |
| G-NFL-03 feature/input schema pinned | PRESENT | schema version + feature-order hash |
| G-NFL-04 governed input adapter | PRESENT | canonical NFLverse resolution + required-field contract |
| G-NFL-05 scorer resolvable in production | PRESENT | verified by import probe; reported in `/health` |
| G-NFL-06 numeric probability package | PRESENT | built through the shared governed-package contract |
| G-NFL-07 dynamic calibration path | PARTIAL | Platt time-split calibration is fitted at training time; no *dynamic* recalibration lane equivalent to the MLB path |
| G-NFL-08 calibrated bounds validated | PARTIAL | bounds derive from a holdout error margin, explicitly named an error margin and not a confidence interval |
| G-NFL-09 determinism/reproducibility | PRESENT | fixed seeds, dataset hash, training-code SHA |
| G-NFL-10 failure-regime / unconditional probability | NOT PROVEN | no failure-path contract evidence located for NFL |
| G-NFL-11 prospective / forward-shadow validation | **ABSENT** | see §3 — this is the principal gap |
| G-NFL-12 calibration-health assessment | **ABSENT** | no NFL calibration-health record |
| G-NFL-13 probability claim auditor compatible | NOT PROVEN | not exercised against NFL output in repository tests |
| G-NFL-14 event decision governor compatible | PARTIAL | governance bridge RPC is invoked; compatibility not independently proven |
| G-NFL-15 event mutex / final refresh | NOT PROVEN | not exercised for NFL |
| G-NFL-16 live cross-sport smoke after deploy | BLOCKED | branch not deployed (see §4) |
| G-NFL-17 no market/generic substitution | PRESENT | enforced and regression-tested (RT-A15, RT-A16) |
| G-NFL-18 `can_execute=false` | PRESENT | invariant across every path |

## 3. The principal gap: no prospective evidence

The difference between MLB and NFL is not scorer quality; it is forward
evidence. MLB carries a forward-shadow capture-and-grade pipeline and a certified
artifact record:

```text
wow_mlb_event_certified_model_artifact
wow_mlb_forward_auto_capture_pregame
wow_mlb_forward_feature_snapshots
wow_mlb_forward_grade_shadow_event
v17/mlb_game_winner_shadow_evaluation.py
v17/mlb_game_winner_shadow_db_runner.py
```

NFL has an artifact registry and a prediction ledger:

```text
wow_nfl_event_fitted_model_artifacts
wow_nfl_event_predictions
```

but **no forward-shadow capture, no grading runner, no calibration-health record,
and no certified-artifact record**. Its validation evidence is a 2025 holdout —
retrospective, not prospective.

Under the active certification policy that is decisive on its own: a model can
pass every retrospective gate and still lack the forward evidence certification
requires. The governance decision to defer is therefore consistent with what the
repository contains.

## 4. Deployment state

The runtime branch is not deployed. Production `/health` exposes the MLB bridge
only, so G-NFL-16 cannot be attempted yet. Deployment does not change any
certification status by itself.

## 5. Recommended next actions, in order

1. Deploy the branch; re-read production `/health` and confirm NFL reports
   `status=UP`, `certification_status=CANDIDATE_REGISTERED_UNCERTIFIED`.
2. Run the live cross-sport smoke scan and the TheRundown normalization/429
   checks (G-NFL-16).
3. Stand up an NFL forward-shadow capture and grading lane mirroring the MLB
   pipeline (G-NFL-11).
4. Produce an NFL calibration-health record from graded forward outcomes
   (G-NFL-12) and a dynamic recalibration path (G-NFL-07).
5. Exercise the probability claim auditor, event decision governor, event mutex
   and final refresh against NFL rows (G-NFL-13/14/15) and the failure-regime
   contract (G-NFL-10).
6. Only then open a certification review. Certification is recorded by adding
   NFL to the governed certification catalog; health reports it automatically and
   separately from bridge status.

Until then:

```text
registered_capability = true
runtime_bridge_status = UP
certification_status  = CANDIDATE_REGISTERED_UNCERTIFIED
supported_regimes     = [REGULAR_SEASON]
can_execute           = false
```
