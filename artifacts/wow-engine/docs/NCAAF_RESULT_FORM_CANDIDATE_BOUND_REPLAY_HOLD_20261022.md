# NCAAF result/form candidate-bound replay, separate source HOLD — 2026-10-22

## Problem fixed in code

The default `v17/candidate_certification_evidence.py` supports the dynamic-team-state family, not `NCAAF_RESULT_FORM_LOGIT_V1`. Rebranding result/form rows as dynamic-team-state or using the prior verifier's source PASS would violate V17 fitted-model governance.

This research branch now provides an **isolated, read-only candidate-bound diagnostic**, `v17/ncaaf_result_form_replay_audit.py`, without altering that default certifier. It is attached to the existing authenticated NCAAF maintenance route installer and exposed only as:

`POST /internal/v17/ncaaf-result-form-candidate-replay/{candidate_id}`

The endpoint is protected using the same `scout_route_auth_dependency(auth_dependency)` used by the existing maintenance routes. It makes database SELECT queries only, bound to the exact candidate family/schema/creation timestamp. **It is not deployed, is not a new top-level Action, does not issue probabilities, and never writes a certification receipt or activates/publishes a model.**

## Replay contract

- Require exact `NCAAF_RESULT_FORM_LOGIT_V1`, `NCAAF_RESULT_FORM_PRIOR_V1`, `NCAAF_SETTLED_PRIOR_RESULTS_RECONSTRUCTION_V1` and `CANDIDATE` lifecycle.
- Reject active/promoted/publishable/executable artifacts, malformed features, market evidence, invalid source manifest hashes, forked same-time event versions, timestamp anomalies or missing prior histories.
- Freeze historical `wow_d1_training_rows` to creation before the exact candidate; preserve latest non-ambiguous source versions and chronological event ordering.
- Recreate the exact standardized logistic candidate and selected identity calibrator with the existing first-party `train_binary_candidate` function. Compare candidate dataset hash, fitted artifact checksum, calibrator hash, validation metrics and the exact model version. Numeric failure is typed as a scorer/replay failure, not `MODEL_UNAVAILABLE`.
- On deterministic replay success, return `NCAAF_RESULT_FORM_DETERMINISTIC_REPLAY_PASS_SOURCE_HOLD`, **not** a certification PASS. The current source regime is retrospective and remains `source_attestation_status=HOLD`.
- Always set `certification_granted=false`, `model_probability=null`, `calibrated_lower_bound=null`, `probability_publishable=false`, `can_execute=false`.

## Verified real validation data (read-only SQL)

Project: `wow-engine-validation`. Exact candidate `44d3dec0-e775-483f-b948-930214b69034`, artifact `NCAAF_RESULT_FORM_LOGIT_V1_23ff66dbe0b0a14c_3accde404100`.

- Training rows: **3,691** with **3,691** distinct event identities. All 3,691 have the expected 9 feature keys, exact source policy, CFBD result-source field, source manifest/event identity, >=3 home and >=3 away prior events, `can_execute=false`, and `market_features_used=false`.
- **3,691/3,691** rows are `historical_reconstruction=true` and `archived_pregame_snapshot=false`.
- **3,691/3,691** were persisted after their target kickoff, despite a reconstructed `feature_as_of` timestamp set strictly before target kickoff. This is not independent point-in-time source attestation.
- Zero candidate-bound certification evidence receipts existed for the selected candidate when queried.
- Earlier 44 prospective CFBD/ESPN-sourced Oct 24 forward inputs are separately stored in this research PR. Their verified existence does not certify the **historical** model fitting population.

## Verification performed

- 25/25 local adversarial / adapter tests PASS, then **25/25 independent GitHub Actions tests PASS** in run https://github.com/gregoryharper84-ship-it/WOW-Dashboard/actions/runs/39866266453.
- Verified authenticated route wiring and code compilation in the GitHub runner.
- Run artifact ID `12463897422`; off-GitHub evidence backup stored under `/WOW EngineeringTeam/WOW_NCAAF_RESULT_FORM_CANDIDATE_BOUND_REPLAY_TESTS_2026-10-22.zip`.
- **A true 3,691-row `audit_from_db` model re-fit has not yet executed** in the deployed runtime; CI tests use synthetic/mocked replay packages and recorded SQL evidence. Do not report numerical live replay PASS.

## Remaining concrete work

1. Run the authenticated read-only `audit_from_db` against the exact candidate in a controlled runtime after the code is reviewed/deployed; assess hash, calibrator, and chronological replay.
2. Establish separately timestamped historical source review/entitlement and prospective validation data, without retroactively upgrading reconstructed feature-as-of flags.
3. Independently certify calibrated event-level lower bounds and compare model-selection vs publication evidence. Keep terminal reducer as sole global publisher.
4. Rich `NCAAF_FEATURES_V1` 18-feature source hydration is a distinct missing capability; no substitution.

`can_execute=false` at every level. No wagering/order/trading functions, no DB mutation, no automatic certification/promotion and no live release.
