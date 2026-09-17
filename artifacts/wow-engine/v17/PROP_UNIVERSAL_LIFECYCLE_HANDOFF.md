# Exact-route certification release handoff

A future route graduation PR must carry all of the following in one reviewable package:

1. Exact `(sport, stat_type, feature_schema_version, model_family, model_artifact_version, artifact_checksum, calibrator_version)` identity.
2. Reviewed route policy ID and thresholds.
3. Artifact-isolated independent forward cohort at or above the policy minimum.
4. `CALIBRATION_CERTIFIED_PASS` evidence hash with Brier, log loss, ECE, bias, and lower-bound reliability.
5. Deterministic replay evidence and source/counterexample review.
6. Independent certification ID and `APPROVED` review state.
7. Exact registry transition carrying that certification ID on the same artifact; no bulk sport/stat update.
8. Model adapter, calibrator adapter, and exact hydration registration.
9. Real canonical `/score-pick-request` canary persisted in `wow_prop_action_canary_receipts` with reconciliation `PASS`.
10. Final production-registration audit returns `PRODUCTION_REGISTERED` and `can_execute=false`.

If any item is missing, the route remains at its typed blocker. No legacy certificate or Phase-A calibrator may be substituted.
