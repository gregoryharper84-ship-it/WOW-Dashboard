# NCAAF exact candidate binding and typed certification gap (2026-10-22)

## Real numerical binding (validation DB; no modifications)

Target: 44 October 24 NCAAF events, as frozen in `data/research/ncaaf_result_form_forward_features_20261022.json`.

Selected existing **research-only** fitted candidate (read-only Supabase validation project):
- `candidate_id=44d3dec0-e775-483f-b948-930214b69034`
- Family: `NCAAF_RESULT_FORM_LOGIT_V1`, schema `NCAAF_RESULT_FORM_PRIOR_V1`.
- Model version: `NCAAF_RESULT_FORM_LOGIT_V1_23ff66dbe0b0a14c_3accde404100`
- Stored artifact checksum `0de8ed68a96e48dcd60773b292c5a46401a090aa21dbdb32de0f5ef4f0f25c1a`; independently recomputed from the actual 9-feature coefficient/scaler JSON via canonical SHA-256 and matched.
- Calibrator: `IDENTITY_RAW_PROBABILITY_V1` selected by `FORWARD_CALIBRATION_SELECTION_V2`, with `test_block_used_for_selection=false`.
- Chronological 2214 train / 738 calibration / 739 untouched test; historical calibrated test Brier 0.1736303859 vs baseline 0.2305853892; ECE 0.0457651072. **Research evidence, not certified calibration or event-level bounds.**
- Verified all 44 frozen real input vectors match the exact artifact schema, finite coefficients, scaler dimensions and selected calibrator. In a private read-only numerical research shadow computation, 44/44 logistic outputs finite and between 0 and 1; range [0.3546884, 0.8326741]. No per-event probabilities were published.
- Model lifecycle `CANDIDATE`, research-screen PASS, `promoted=false`, `active=false`, `probability_publishable=false`, `can_execute=false`.

## Exact blocker: candidate-bound certification pipeline does not cover this family

`v17/candidate_certification_evidence.py` explicitly requires
`source_policy_id == TEAM_STATE_DYNAMIC_PRIOR_ONLY_V1` and model family suffix
`_DYNAMIC_TEAM_STATE_LOGIT_V2`. The exact NCAAF result/form source policy is
`NCAAF_SETTLED_PRIOR_RESULTS_RECONSTRUCTION_V1`, which is **not** supported by that verifier.
The current first-party `model_source_entitlements.SOURCES` also lacks that reconstruction policy as an entitled source.

Read-only DB check found **zero** candidate-bound replay receipts for this selected candidate. There are 3,691 existing source-backed `wow_d1_training_rows` for its family, all recorded before this candidate; their `historical_reconstruction=true` status is explicit. Treat the retrospective feature-as-of values as reconstructive statements, **not** independently archived pregame captures.

The current route must produce **typed `CANDIDATE_REPLAY_CONTRACT_UNSUPPORTED`**, not silently certify a different source regime. Source entitlement review, exact frozen-corpus replay, chronological contamination tests, time attestation, independent certification and bound construction remain separate.

## Implementation in this PR

- Metadata-only receipt (no fitted coefficients committed):
  `data/research/ncaaf_result_form_candidate_binding_20261022.json`
- Fail-closed validator supporting optional *private* exact candidate artifact:
  `scripts/ncaaf_result_form_candidate_binding_audit.py`
- 13 deterministic negative/positive tests:
  `tests/test_ncaaf_result_form_candidate_binding_audit.py`
- One-shot GitHub Actions regression and archived receipt:
  `.github/workflows/wow-research-ncaaf-fitted-binding-once.yml`

`probability_publishable=false`; `rank_eligible=false`; `can_execute=false`.
No source policy changed, no credential exposed, no scorer deployment, DB write, candidate promotion, wager or order.
