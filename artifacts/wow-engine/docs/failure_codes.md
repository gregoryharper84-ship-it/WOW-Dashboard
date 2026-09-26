# WOW V17 Canonical Failure-Code Registry

This file is the repository source of truth for governed typed failure/status codes introduced or relied on by the V17 full-board stabilization work. New typed failure codes must be registered in the same change that introduces them.

`can_execute=false` remains invariant. Market/evidence failures never imply a sporting-model capability failure.

| Code | Owning lane/stage | Meaning | Rank eligible? |
|---|---|---|---:|
| `MODEL_UNAVAILABLE` | model capability | Exact required fitted specialist/artifact/adapter is absent for the route. Never use for data, market, scorer, repository, or editor failures. | No |
| `MODEL_INPUTS_INSUFFICIENT` | model readiness | Fitted capability exists but required candidate-specific inputs are missing/insufficient. Includes a missing/invalid required calibration artifact for an otherwise available multisport scorer. | No |
| `MODEL_SCORER_FAILED` | model invocation | Selected model was invoked but threw, timed out, transport-failed, or returned no valid completion. | No |
| `MODEL_OUTPUT_INVALID` | model validation | Selected model returned malformed, non-numeric, schema-invalid, impossible, or non-normalized output. | No |
| `EVENT_ALREADY_STARTED` | slate/final refresh | Pregame candidate has started or completed and cannot remain on a pregame leaderboard. | No |
| `MARKET_DATA_UNOBTAINABLE` | market/evidence | Required market evidence could not be acquired. Does not erase an already-completed sporting probability. | Sporting probability may remain eligible; market/value lane blocked |
| `AUTH_FAILED` | market acquisition | Provider rejected the configured credential (for example HTTP 401/403). Independent from `model_status`. | Does not decide sporting rank |
| `CREDENTIAL_UNCONFIGURED` | market acquisition | No credential is configured for a provider that requires one. | Does not decide sporting rank |
| `RATE_LIMITED` | market acquisition | Provider refused the request due to rate/quota limits. | Does not decide sporting rank |
| `IDENTITY_UNRESOLVED` | event identity | Official canonical event identity has not been resolved. Provider IDs remain aliases only. | No |
| `OFFICIAL_EVENT_ID_REQUIRED_FOR_CANONICALIZATION` | event identity | Canonical event key cannot be constructed because authoritative official event identity is absent. | No |
| `RUN_INVALID_EVIDENCE_BINDING` | producer→consumer evidence handoff | A required field is demonstrably populated in the controlling sporting package/canonical evidence but downstream governance reports that same field as missing/not called. The run is invalid rather than an ordinary no-pick; the completed sporting package is preserved for diagnosis but cannot rank/publish. | No |
| `DISCOVERED_ROW_MISSING_TERMINAL_DISPOSITION` | row reconciliation | A discovered candidate reached publication without exactly one terminal disposition. The row must remain visible and the run is incomplete. | No |
| `FULL_BOARD_DISCOVERY_ROW_MISSING_CANDIDATE_ID` | row reconciliation | Discovery emitted a row without a stable candidate identity. | No |
| `FULL_BOARD_FINAL_ROW_MISSING_CANDIDATE_ID` | row reconciliation | Final-stage output cannot be mapped back to a discovered candidate. | No |
| `PROBABILITY_PACKAGE_NOT_VALID` | publication chain | Governing numeric probability package validation has not passed. | No |
| `DYNAMIC_CALIBRATION_NOT_COMPLETE` | publication chain | Governed dynamic calibration has not completed. | No |
| `PROBABILITY_AUDIT_NOT_COMPLETE` | publication chain | Probability audit has not passed after model/calibration completion. | No |
| `EVENT_GOVERNOR_NOT_COMPLETE` | publication chain | Event-governance/mutex stage has not passed. | No |
| `CALIBRATED_PROBABILITY_OR_LOWER_BOUND_MISSING` | publication chain | Required governed calibrated probability/bound fields are absent. | No |
| `FINAL_REFRESH_NOT_COMPLETE` | publication chain | Final pre-publication status/freshness refresh has not passed. | No |
| `TERMINAL_AUTHORITY_OR_EXECUTION_INVARIANT_NOT_PROVEN` | terminal reduction | V17 terminal authority or `can_execute=false` invariant is not proven. | No |
| `RANK_ELIGIBLE_OR_PROBABILITY_PUBLISHABLE_NOT_PROVEN` | publication chain | All diagnostic stages may be present, but final governed rank/publication proof is absent. | No |
| `CALIBRATION_ARTIFACT_INVALID_OR_UNAVAILABLE` | multisport calibration | WNBA/NHL/soccer/tennis/MMA raw sporting model exists, but its required fitted calibration artifact is missing, malformed, unhealthy, uncertified, wrong-sport, or wrong-model. Surfaced as `MODEL_INPUTS_INSUFFICIENT`. | No |
| `CALIBRATION_HISTORY_NOT_PROVEN` | multisport terminal calibration | A raw/provisional model package reached terminal governance without proof of historical calibration. | No |
| `CALIBRATION_ARTIFACT_NOT_CERTIFIED` | multisport terminal calibration | Calibration artifact has not proven the required certification status. | No |
| `CALIBRATION_HEALTH_NOT_PASS` | multisport terminal calibration | Fitted calibrator health is not PASS. | No |
| `CALIBRATION_STATUS_NOT_PASS` | multisport terminal calibration | Candidate calibration stage did not complete successfully. | No |
| `CALIBRATION_HISTORY_SAMPLE_INSUFFICIENT` | multisport terminal calibration | Historical calibration sample is below the lane minimum required by the artifact contract. | No |
| `CALIBRATION_ARTIFACT_FINGERPRINT_INVALID` | multisport terminal calibration | Immutable calibration artifact fingerprint is missing or invalid. | No |
| `CALIBRATION_FIT_END_INVALID_OR_FUTURE_LEAKAGE` | multisport terminal calibration | Calibration fit cutoff is invalid or occurs after the immutable model timestamp. | No |
| `CALIBRATION_MODEL_FAMILY_MISMATCH` | multisport calibration | Calibration artifact targets a different model family than the controlling specialist package. Surfaced as `MODEL_INPUTS_INSUFFICIENT`. | No |
| `CALIBRATION_MODEL_VERSION_MISMATCH` | multisport calibration | Calibration artifact targets a different model version than the controlling specialist package. Surfaced as `MODEL_INPUTS_INSUFFICIENT`. | No |
| `TEAM_EVENT_SPECIALIST_ARTIFACT_NOT_CERTIFIED` | fitted team/event certification | No current immutable certification receipt proves the exact sport/league fitted artifact. A registered/importable bridge or caller-supplied calibrator is not sufficient. | No |
| `TEAM_EVENT_SPECIALIST_CERTIFICATION_REVOKED` | fitted team/event certification | The latest immutable certification receipt explicitly revokes the prior sport/league specialist certification. | No |
| `TEAM_EVENT_CERTIFICATION_REGISTRY_UNAVAILABLE` | fitted team/event certification registry | The service-role certification-registry RPC could not be read. Infrastructure/registry failure; never rewrite to `MODEL_UNAVAILABLE`. | No |
| `TEAM_EVENT_CERTIFICATION_REGISTRY_INVALID_RESPONSE` | fitted team/event certification registry | The certification registry returned an unusable response shape. | No |
| `TEAM_EVENT_INDEPENDENT_VERIFICATION_NOT_PASS` | fitted team/event independent verification | The exact fitted artifact/calibration pair has not passed the required independent numerical verification. | No |
| `TEAM_EVENT_SPECIALIST_NOT_CERTIFIED` | fitted team/event certification | A proof record exists, but its state is not `CERTIFIED`. | No |
| `TEAM_EVENT_PROBABILITY_PACKAGE_INVALID` | common fitted-specialist package | The common sport-specialist probability envelope is not an object. | No |
| `TEAM_EVENT_PROBABILITY_PACKAGE_NOT_VALID` | common fitted-specialist package | The controlling specialist did not mark its governed package valid after sport-specific validation. | No |
| `TEAM_EVENT_CERTIFICATION_SPORT_MISMATCH` | common fitted-specialist package | Package sport does not match the immutable certification receipt. | No |
| `TEAM_EVENT_CERTIFICATION_SPECIALIST_MISMATCH` | common fitted-specialist package | Package controlling-specialist identity does not match certification. | No |
| `TEAM_EVENT_CERTIFICATION_MODEL_VERSION_MISMATCH` | common fitted-specialist package | Package model version differs from the certified model version. | No |
| `TEAM_EVENT_CERTIFICATION_ARTIFACT_MISMATCH` | common fitted-specialist package | Package artifact identity differs from the certified artifact. | No |
| `TEAM_EVENT_CERTIFICATION_CALIBRATION_MISMATCH` | common fitted-specialist package | Package calibration method differs from the certified calibration contract. | No |
| `RAW_PROBABILITY_INVALID` | common fitted-specialist package | Raw probability is missing, non-numeric, non-finite, or outside `[0,1]`. | No |
| `CALIBRATED_PROBABILITY_INVALID` | common fitted-specialist package | Calibrated probability is missing, non-numeric, non-finite, or outside `[0,1]`. | No |
| `CALIBRATED_LOWER_BOUND_INVALID` | common fitted-specialist package | Governed lower bound is missing, non-numeric, non-finite, or outside `[0,1]`. | No |
| `CALIBRATED_UPPER_BOUND_INVALID` | common fitted-specialist package | Governed upper bound is missing, non-numeric, non-finite, or outside `[0,1]`. | No |
| `CALIBRATED_BOUNDS_ORDER_INVALID` | common fitted-specialist package | Bounds violate `lower_bound <= calibrated_probability <= upper_bound`. | No |
| `MODEL_TIMESTAMP_INVALID` | common fitted-specialist freshness | Model timestamp is missing, malformed, or timezone-naive. | No |
| `LATEST_MATERIAL_UPDATE_TIMESTAMP_INVALID` | common fitted-specialist freshness | Latest-material-update timestamp is missing, malformed, or timezone-naive. | No |
| `MODEL_STALE_AFTER_MATERIAL_UPDATE` | common fitted-specialist freshness | `model_timestamp < latest_material_update_timestamp`; exact rerun required before publication. | No |
| `CAN_EXECUTE_MUST_BE_FALSE` | global safety | Certification/package attempted to set `can_execute` to anything other than false. | No |
| `RUNDOWN_SPORT_ID_UNRESOLVED` | TheRundown acquisition | Catalog access succeeded but the target sport could not be resolved to the provider sport ID. | Does not decide sporting rank |
| `CATALOG_ACCESS_REQUIRED_FIRST` | TheRundown health | Strict provider health did not attempt event access because catalog authentication/access failed first. | Does not decide sporting rank |
| `DATE_MUST_BE_YYYY_MM_DD` | diagnostic API | Invalid date supplied to a bounded health/discovery diagnostic route. | N/A |
| `INVALID_PAGINATION` | diagnostic API | Page/page-size request is invalid. | N/A |

## Multisport calibration artifact detail blockers

The following detail blockers remain underneath the registered `MODEL_INPUTS_INSUFFICIENT` / `CALIBRATION_ARTIFACT_INVALID_OR_UNAVAILABLE` class and must never be rewritten to `MODEL_UNAVAILABLE`: `CALIBRATION_ARTIFACT_MISSING`, `CALIBRATION_ARTIFACT_SPORT_MISMATCH`, `CALIBRATION_CERTIFICATION_NOT_PASS`, `CALIBRATION_TRAINING_N_INSUFFICIENT`, `CALIBRATION_METHOD_MISSING`, `CALIBRATION_VERSION_MISSING`, `CALIBRATION_SOURCE_DATA_HASH_INVALID`, `CALIBRATION_SPLIT_HASH_INVALID`, `CALIBRATION_FIT_END_INVALID`, `CALIBRATION_BRIER_INVALID`, `CALIBRATION_ERROR_INVALID`, `BINARY_CALIBRATION_ARTIFACT_TYPE_INVALID`, `MULTICLASS_CALIBRATION_ARTIFACT_TYPE_INVALID`, `PLATT_COEFFICIENTS_INVALID`, `CALIBRATION_RESIDUAL_QUANTILE_INVALID`, and the outcome-specific soccer calibration record/coefficient/residual blockers.

## Fitted team/event certification detail blockers

The following proof-validation details remain underneath `TEAM_EVENT_SPECIALIST_ARTIFACT_NOT_CERTIFIED`, `TEAM_EVENT_INDEPENDENT_VERIFICATION_NOT_PASS`, or the registered common package class and must not be rewritten to `MODEL_UNAVAILABLE`: `TEAM_EVENT_CERTIFICATION_INVALID`, `TEAM_EVENT_CERTIFICATION_FIELD_MISSING`, `TEAM_EVENT_CERTIFICATION_HASH_INVALID`, `TEAM_EVENT_CERTIFICATION_TIMESTAMP_INVALID`, `TEAM_EVENT_CERTIFICATION_CAN_EXECUTE_FORBIDDEN`, `TEAM_EVENT_CERTIFICATION_SPORT_UNSUPPORTED`.

## Existing provider/discovery statuses preserved by V17

The following existing statuses remain authoritative in their owning modules and are intentionally not collapsed into the generic codes above: `NO_CONFIGURED_DISCOVERY_FEED`, `PROVIDER_REQUEST_FAILED`, `PROVIDER_RATE_LIMITED`, `PROVIDER_SCHEMA_FAILURE`, `DISCOVERY_BUDGET_EXHAUSTED`, `EVENT_WRONG_DATE`, `EVENT_STARTED_OR_FINAL`, `EVENT_CANCELLED_OR_POSTPONED`, and `EVENT_IDENTITY_UNRESOLVED`.

## Registry rule

A new code requires, in the same change: code name, owning lane/stage, exact condition, whether it affects sporting probability/rank, and a regression test. Provider-specific detail codes may be preserved underneath a registered class; they must never be rewritten into `MODEL_UNAVAILABLE` unless the fitted model capability itself is truly absent.