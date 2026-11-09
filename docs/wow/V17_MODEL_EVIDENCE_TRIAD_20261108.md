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
