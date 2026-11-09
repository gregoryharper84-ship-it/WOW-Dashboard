# WOW V17 — Three-part model evidence contract

**Objective:** #2536. **Owner:** WOW Betting Intelligence (scope and acceptance); AMRT Engineering (implementation); SIRT (independent adversarial review); Independent Verification (final acceptance). **Status:** WORKING_NOT_COMPLETE.

This is a read-only, fail-closed model evidence policy, **not** evidence that any model was certified or deployed. The code in `v17/model_evidence_bundle_contract.py` covers all 13 team/event sports declared by `EXPECTED_TEAM_EVENT_SPORTS` and exact PLAYER_PROP and WEATHER_CONTRACT families.

## Universal requirement

Every model-serving version must bind all three components to the **same** sport, model family, artifact version and feature schema:

1. **Fitted artifact:** immutable artifact ID/checksum, training-dataset hash, code SHA, training cohort, independently checked payload and training provenance. An unfitted source candidate or model-family string is not an artifact.
2. **Calibrator and bounds:** exact artifact-checksum binding, immutable calibrator checksum, separate chronological calibration cohort, no holdout overlap, audited Brier/log loss/calibration bias, disjoint validation evidence, fitted calibration method and tested lower-bound method. A PLATT/ISOTONIC label alone is not valid calibrated-lower-bound proof.
3. **Pregame corpus:** event-specific official identity, exact sport/participants or player/offer or station/contract, source and feature snapshot IDs/hashes, captured-before-start source/feature timestamps, independently verified raw source, matched feature schema, event status/settlement rules and rehydration after material changes. A date-only midnight-UTC feature timestamp is **not** tipoff attestation.

**Full model** requires the controlling specialist's independent certification *after* the complete triad, valid calibrated bounds, terminal receipt and final pregame freshness. The bundle validator itself always returns `probability_publishable=false`, `rank_eligible=false` and `can_execute=false` regardless of its component results; it cannot promote or certify.

Missing and invalid components use distinct blockers: `MODEL_BUNDLE_ARTIFACT_MISSING`, `MODEL_BUNDLE_CALIBRATOR_MISSING`, `MODEL_BUNDLE_PREGAME_CORPUS_MISSING`, and component-specific `*_INVALID_OR_UNVERIFIED`. Missing/superseded/started identities are not replaced by another sport's artifact or an adjacent prop offer.

## Read-only validation-database inventory

Project: `wow-engine-validation` (`iczfhsmjrrafhvcpmqhr`). Snapshot inspected 2026-11-08/09 UTC. **Counts are presence only. None establishes exact-version binding, reproducibility, current pregame admissibility or SIRT certification.**

| Model lane | Artifact evidence | Calibrator evidence | Historical/pregame corpus | Remaining proof |
|---|---|---|---|---|
| **NFL team/event** | 10 fitted rows (one active/promoted) | 10 generic NFL calibrators (one active/promoted) | 1,547 pregame features, 1,547 training games, 109 source snapshots | Bind active version to cohort, full forward grades and independent release; preserve NFL Pick'em |
| **NBA team/event** | 112 basketball artifact rows; latest model 7,101 train/1,785 holdout, SHADOW | 112 paired basketball calibrators; latest 6,944 rows | 8,886 pregame features, 9,058 settled training games | Exact calibration/corpus replay in draft #2535; missing immutable bundle provenance and real tipoff attestation |
| **NHL team/event** | 36 D1 development candidates with artifact payloads | D1 candidate calibrator payloads exist | No dedicated `wow_nhl_*` tables in this validation schema | Persist official NHL source, source-timed features, fitted independent artifact/calibration, verified serving |
| **NCAAF team/event** | 141 D1 development candidates; zero dedicated fitted artifacts | D1 candidate calibrator payloads; zero dedicated fitted calibrators | 4,344 training games, 351 source snapshots, **zero** `wow_ncaaf_training_features` | Rebuild temporal feature corpus and governed fitted serving bundle |
| **NCAAB team/event** | 62 D1 development candidates with artifact payloads | D1 candidate calibrator payloads | No dedicated `wow_ncaab_*` tables in validation schema | Official identity/feature persistence, calibrated fitted specialist and live adapter |
| **MLB team/event** | One active/promoted fitted event-artifact row | Model artifact's `calibrator_id` currently NULL; historical MLB calibration tables exist but exact active bundle unverified | 2,454 forward feature snapshots, 9,900 multi-season training rows, 140 source snapshots | Verify exact calibration binding and live event-source/feature acceptance; do not relabel MLB as missing model |
| **WNBA team/event** | 109 basketball artifacts, none active/promoted | 109 basketball calibrators, none active/promoted | 2,144 pregame feature rows, 2,233 training games | Exact-version independent verification and governed promotion |
| **Soccer team/event** | 398 D1 development candidates; source- and league-specific | Candidate calibrator payloads | No sport-dedicated pregame table confirmed in inspected schema | 1X2 league/competition settlement identity, source-timed cohort, receipt |
| **Tennis team/event** | 64 D1 development candidates | Candidate calibrator payloads | No dedicated pregame table confirmed | Individual tournament/retirement settlement, surface, timestamps and receipt |
| **PGA / MMA / Boxing / Cricket** | No D1 candidate rows found for these sports in the inspected D1 table | No associated D1 calibrator proof | No sport-dedicated pregame corpus confirmed | Build independent source, fit, calibrate and exact-settlement lane; do not borrow another sport's coefficients |
| **Player/scalar props** | 33 fitted prop artifacts, 14 active/promoted, across MLB/NFL/NBA/WNBA | Distinct exact-version calibration/reliability binds not yet reconciled for all 33 | 4,681 prediction rows carry source snapshot IDs; 4,658 carry feature hashes | Reconcile each `sport/stat/period/line/direction` and version; a prediction row does not prove immutable prelock source |
| **Weather contracts** | No weather fitted-artifact registry proven in inspected tables | `wow_kalshi_weather_calibration_profiles`: zero rows | 2,615 source snapshots, 421 historical prediction rows | Certified weather specialist must own fitted version, station/contract-specific calibration and prelock evidence |

Other sport or market families found in the registry must join the same audit, never disappear because an enumerator returns no model. Supplied odds, tipsters and market prices are only external evidence, not artifact/calibrator/pregame-corpus replacements.

## Implementation / independent acceptance

- **Engineering:** wire actual exact-version artifact/calibrator/source readers into the pure validator at each certified scorer and Action path. All must preserve native error categories (model unavailable, input insufficient, scorer failed, source missing, stale).
- **SIRT:** challenge cross-sport and cross-version substitutions, post-start source use, retrospective backfills, training/holdout leakage, bogus zero overlap, stale injuries, invalid lower bounds and parent-correlated line props.
- **Independent Verification:** prove one valid version and one missing-component negative canary per supported model family, with immutable pregame receipt and terminal reducer response. Record evidence/PR/head/deployed SHA separately.
- **Reducer:** V17_TERMINAL_REDUCER remains sole terminal authority. No wager or order execution. No automatic rank eligibility, certification, promotion or hold waiver.

Until connected readers and independent acceptance prove each specific lane, the universal contract is **implemented as a reusable contract and development-lane declaration**, not as a claim that every production scorer is already protected by the new validator. Do not merge/deploy or mark COMPLETE on green CI alone.

## Registry reader layer (read-only)

`v17/model_evidence_registry_readers.py` connects the pure validator to governed persistence. It reads only; it never writes, fits, calibrates, certifies, promotes or scores. `assess_registry_model_bundle(db, scope)` returns the merged contract verdict plus `component_sources` and `missing_component_fields`.

Guarantees of this layer:

- **No registry column is verification.** `payload_checksum_verified`, `training_provenance_verified`, `chronological_replay_verified`, `calibrated_lower_bound_verified` and the four pregame verification flags are set only from an explicit `true` inside an immutable `wow_team_event_specialist_certifications.verification_receipt` for the exact `sport`/`model_version`/`artifact_sha256`/`feature_schema_version` with `CERTIFIED` + `PASS`. No receipt yields `MODEL_BUNDLE_VERIFICATION_RECEIPT_MISSING`, and the flags are overridden to `false` rather than defaulted to trust.
- **No substitution.** A missing event row is not replaced by another event, an adjacent prop offer, a sibling sport or a model-family name.
- **Typed failures.** Unconfigured lanes report `MODEL_BUNDLE_<COMPONENT>_SOURCE_NOT_CONFIGURED`, unreadable stores `*_READ_FAILED`, duplicate exact identities `*_AMBIGUOUS`, unlinkable calibrators `MODEL_BUNDLE_CALIBRATOR_LINK_UNAVAILABLE`, and each absent column `MODEL_BUNDLE_<COMPONENT>_FIELD_UNAVAILABLE:<field>`.

Configured lanes and their observed gaps (presence only, not certification):

| Lane | Artifact | Calibrator | Pregame corpus |
|---|---|---|---|
| NFL | `wow_nfl_event_fitted_model_artifacts` | `wow_calibrators` via artifact `calibrator_id`; no artifact checksum binding | `wow_nfl_pregame_feature_rows` is a training matrix keyed by gameday, so tipoff, source snapshot identity and source payload hash are unavailable |
| MLB | `wow_mlb_event_fitted_model_artifacts` | `wow_calibrators` via artifact `calibrator_id`; no artifact checksum binding | No per-event pregame table configured |
| NBA / WNBA | `wow_basketball_team_event_model_artifacts`; no `training_dataset_hash` or `training_code_sha` column | `wow_basketball_team_event_calibrators`; no checksum, cohort hash or holdout separation | `wow_nba_pregame_feature_rows` / `wow_wnba_pregame_feature_rows`; `as_of` and feature hash only, no tipoff, source snapshot or event status |
| Player props | `wow_prop_fitted_model_artifacts` | Not configured: the registry stores only a `calibrator_version` string | Not configured |
| Weather contracts | Not configured: no fitted weather artifact registry | `wow_kalshi_weather_calibration_profiles` is an unfitted profile table | Not configured |
| NHL, NCAAB, NCAAF, SOCCER, TENNIS, PGA, MMA, BOXING, CRICKET | Development candidates live in the shared D1 candidate registry, which is research evidence with no dedicated pregame corpus and no verification receipts, so no reader is declared |

Consequently every configured lane currently returns `BLOCKED`. That is the expected, evidence-based result, not a regression: the registered tables cannot supply an exact-version, independently verified triad. The report names the absent component and the absent field for each lane instead of emitting a probability or an empty success.

## Scoring-boundary enforcement

`v17/model_evidence_boundary.py` applies the reader verdict at the single dispatch point every certified team-event scorer and the Action path already pass through: `score_registered_team_event_request` (also installed as the base `score_team_event_request`, so sport bridges and their governance overlays cannot bypass it).

- **Exact served identity.** `build_evidence_scope` builds the scope from the identity the result actually served (`model_family`, `model_artifact_version`/`model_version`, `feature_schema_version`, `immutable_model_timestamp`/`model_timestamp`) plus the request's `official_event_id` and `event_start_time_utc`. A repository contract supplies a value only where the family declares one (MLB artifact version and feature schema, NFL feature schema); nothing is inferred from a sport string or another model. Unresolved fields yield `MODEL_EVIDENCE_TRIAD_IDENTITY_UNRESOLVED:<field>`.
- **Typed blockers, no substitution.** An incomplete triad reports `MODEL_EVIDENCE_TRIAD_INCOMPLETE` together with every component code the reader produced; an unreadable store reports `MODEL_EVIDENCE_TRIAD_READER_UNAVAILABLE`. The report is attached as `model_evidence_triad` with `evidence_triad_status` and merged into `publication_blockers`, preserving any upstream blocker and its order.
- **What is withheld.** `probability_publishable=false`, `rank_eligible=false`, `terminal_label`/`terminal_ceiling=MODEL_QUALIFIED_HOLD`, and the reducer input is set to `status=HOLD`, `terminal_output=MODEL_QUALIFIED_HOLD`, `global_terminal_reducer=V17_TERMINAL_REDUCER`, `evidence_triad_complete=false`. `can_execute=false` everywhere. Probabilities, bounds, feature receipts and market status are left exactly as scored, so the immutable diagnostic record survives; only the publication and terminal claim is withheld. An already-stricter upstream terminal (`RESEARCH_INTEREST`, `NO_LOW_PROBABILITY`, an infrastructure hold) is preserved rather than replaced by `MODEL_QUALIFIED_HOLD`, and only approval-asserting labels are downgraded.
- **Publication gate.** `evaluate_team_event_official_publication` refuses a payload that cannot prove its triad, adding `TEAM_EVENT_MODEL_EVIDENCE_TRIAD_NOT_PROVEN`. The requirement is derived from the governed `TEAM_EVENT_INPUT_CONTRACTS` manifest, not from the payload's `evidence_triad_required` flag: clearing or omitting that flag on a governed sport is still held. Completeness is accepted only from a report sealed at the scoring boundary (`report_id` + per-process keyed digest); an unsealed claim, an edited report or a report sealed for another sport is rejected. Payloads naming no governed sport and no team/event family keep the legacy contract.
- **Failure taxonomy.** The gate never converts a triad gap into `MODEL_UNAVAILABLE`: the model was available, invoked and scored. Model failures keep their own codes.
- **Test seam.** `EVIDENCE_ASSESSOR` is `None` in production so the governed readers always run; acceptance fixtures set it to declare a verified registry without fabricating rows.

Boundary coverage lives in `tests/test_v17_model_evidence_boundary.py`. Because every configured lane still returns `BLOCKED`, registered scorers now score and withhold instead of publishing; that remains the intended evidence-based state rather than a certification claim.

### Prop and Kalshi weather serving

The same readers now govern the two other serving paths that emit a fitted probability.

- **Player props.** `prop_universal_forward_evidence._persist_prediction` gates the payload before persistence using a `PLAYER_PROP` scope built from the row's own `sport`, `stat_type`, `event_id`, `event_start_time`, `model_timestamp`, `model_family`, `model_artifact_version` and `feature_schema_version`. A row whose artifact, calibrator or event-specific pregame evidence is missing is persisted with `probability_publishable=false`, `triad_blockers` and a sealed report, while its calibrated probability, bounds and calibration evidence are preserved. Missing identity is reported as `MODEL_EVIDENCE_TRIAD_IDENTITY_UNRESOLVED:<field>`, never guessed.
- **Kalshi weather.** `reduce_kalshi_weather_prediction_v17` gates its successful reduction using a `WEATHER` scope. No weather lane declares an artifact, calibrator and pregame reader, so a weather reduction now returns `probability_publishable=false`, `rank_eligible=false` and `MODEL_EVIDENCE_TRIAD_LANE_NOT_CONFIGURED` in `probability_blockers`, while preserving `p_yes`, `p_no`, the bounds and the market/edge reconciliation.
- **Undeclared lanes never fabricate a reader.** `lane_configured` answers from the declared lane table; an undeclared model kind or sport reports the three `*_SOURCE_NOT_CONFIGURED` blockers instead of reading a look-alike table or a research candidate registry.

Prop/weather coverage lives in `tests/test_v17_model_evidence_prop_weather.py`; the weather reducer's own expectations were updated to the withheld-until-verified policy. Withholding publication here is an evidence statement, not a claim that these probabilities are wrong.
