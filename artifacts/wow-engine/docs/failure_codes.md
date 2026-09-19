# WOW V17 Canonical Failure-Code Registry

This file is the repository source of truth for governed typed failure/status codes introduced or relied on by the V17 full-board stabilization work. New typed failure codes must be registered in the same change that introduces them.

`can_execute=false` remains invariant. Market/evidence failures never imply a sporting-model capability failure.

| Code | Owning lane/stage | Meaning | Rank eligible? |
|---|---|---|---:|
| `MODEL_UNAVAILABLE` | model capability | Exact required fitted specialist/artifact/adapter is absent for the route. Never use for data, market, scorer, repository, or editor failures. | No |
| `MODEL_INPUTS_INSUFFICIENT` | model readiness | Fitted capability exists but required candidate-specific inputs are missing/insufficient. | No |
| `MODEL_SCORER_FAILED` | model invocation | Selected model was invoked but threw, timed out, transport-failed, or returned no valid completion. | No |
| `MODEL_OUTPUT_INVALID` | model validation | Selected model returned malformed, non-numeric, schema-invalid, impossible, or non-normalized output. | No |
| `EVENT_ALREADY_STARTED` | slate/final refresh | Pregame candidate has started or completed and cannot remain on a pregame leaderboard. | No |
| `MARKET_DATA_UNOBTAINABLE` | market/evidence | Required market evidence could not be acquired. Does not erase an already-completed sporting probability. | Sporting probability may remain eligible; market/value lane blocked |
| `AUTH_FAILED` | market acquisition | Provider rejected the configured credential (for example HTTP 401/403). Independent from `model_status`. | Does not decide sporting rank |
| `CREDENTIAL_UNCONFIGURED` | market acquisition | No credential is configured for a provider that requires one. | Does not decide sporting rank |
| `RATE_LIMITED` | market acquisition | Provider refused the request due to rate/quota limits. | Does not decide sporting rank |
| `IDENTITY_UNRESOLVED` | event identity | Official canonical event identity has not been resolved. Provider IDs remain aliases only. | No |
| `OFFICIAL_EVENT_ID_REQUIRED_FOR_CANONICALIZATION` | event identity | Canonical event key cannot be constructed because authoritative official event identity is absent. | No |
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
| `RUNDOWN_SPORT_ID_UNRESOLVED` | TheRundown acquisition | Catalog access succeeded but the target sport could not be resolved to the provider sport ID. | Does not decide sporting rank |
| `CATALOG_ACCESS_REQUIRED_FIRST` | TheRundown health | Strict provider health did not attempt event access because catalog authentication/access failed first. | Does not decide sporting rank |
| `DATE_MUST_BE_YYYY_MM_DD` | diagnostic API | Invalid date supplied to a bounded health/discovery diagnostic route. | N/A |
| `INVALID_PAGINATION` | diagnostic API | Page/page-size request is invalid. | N/A |

## Existing provider/discovery statuses preserved by V17

The following existing statuses remain authoritative in their owning modules and are intentionally not collapsed into the generic codes above: `NO_CONFIGURED_DISCOVERY_FEED`, `PROVIDER_REQUEST_FAILED`, `PROVIDER_RATE_LIMITED`, `PROVIDER_SCHEMA_FAILURE`, `DISCOVERY_BUDGET_EXHAUSTED`, `EVENT_WRONG_DATE`, `EVENT_STARTED_OR_FINAL`, `EVENT_CANCELLED_OR_POSTPONED`, and `EVENT_IDENTITY_UNRESOLVED`.

## Registry rule

A new code requires, in the same change: code name, owning lane/stage, exact condition, whether it affects sporting probability/rank, and a regression test. Provider-specific detail codes may be preserved underneath a registered class; they must never be rewritten into `MODEL_UNAVAILABLE` unless the fitted model capability itself is truly absent.
