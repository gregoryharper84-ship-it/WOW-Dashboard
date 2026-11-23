# NFL V17 exact-version pregame certification evidence — validation only
Date observed: 2026-11-18 (America/Chicago)
Incident: #2020
Objective ID: WOW-P0-NFL-CERT-EVIDENCE-20261118
Status: WORKING_NOT_COMPLETE
Owners: Engineering/AMRT (implementation), NFL fitted specialist (model), SIRT (independent challenge), Independent Verification (acceptance)
Project observed: wow-engine-validation / Supabase `iczfhsmjrrafhvcpmqhr`
Production/deployment/editor status: NOT VERIFIED BY THIS EVIDENCE
Terminal authority: `V17_TERMINAL_REDUCER`; `can_execute=false`

## Existing authoritative files
- `v17/nfl_forward_shadow.py`: canonical pregame selection, validated settlement, 10-bin ECE, fixed forward-health gates.
- `v17/nfl_daily_stage_receipts.py`: scorer-producer calibration operation receipt readback; current publication requires exact producer and refresh evidence.
- `v17/NFL-CERTIFICATION-CHECKLIST-2026-11-11.md`: G1–G7 authority and unresolved G4/G5 order.
- `scripts/v17_nfl_pregame_evidence_chain_audit.sql`: read-only source/prediction/artifact/calibrator/certification links.
- `scripts/v17_nfl_exact_version_calibration_replay.sql`: read-only exact-model-version independent arithmetic; not a certification transaction.

## Executed observations
Latest health (2026-11-18T20:46:56Z): `graded_n=107`, `minimum_forward_required=100`, `status=PASS`, `certification_recommendation=FORWARD_EVIDENCE_PASS`; global Brier 0.2196497697, log loss 0.6266274582, ECE 0.0394215935, bias +0.0376285693, lower-bound reliability gap +0.0182895138.

One exact version in `wow_nfl_forward_verified_grades`: `NFL_EVENT_LOGREG_PLATT_V1_5d603a873fbb`. 107 grade rows, 107 unique official games, 107 distinct prediction IDs; recreated Brier, log loss, ECE, bias and bound gap match global health at this snapshot. Zero invalid probabilities and zero stored-metric disagreements. `EXACT_VERSION_METRICS_WITHIN_EXISTING_THRESHOLDS_REQUIRES_INDEPENDENT_REVIEW` is a research finding, NOT governed certification.

Pregame evidence-chain audit: zero missing model receipt identities, invalid source/feature links, timestamp/freshness conflicts, artifact mismatches, and calibrator mismatches *under the SQL's declared metadata predicates*. 107/107 rows lack a matching independently approved `wow_team_event_specialist_certifications` receipt. This evidence does not prove feature-by-feature numerical consumption.

Active NFL champion artifact `7fee1209-0bed-4bd0-aa0f-fc7a881eff80`, checksum `aa41eefbbe93f94f9af0401a2c0a7bd9c9505569e8c3754ac396d552a0ead04b`, calibrator `756379bd-f557-4508-8ecb-91a5d336ec64`. Artifact/calibrator are active/promoted; calibrator validation/health PASS. Artifact `probability_publishable=false`. Runtime capability row reports `AVAILABLE` with `MODEL_QUALIFIED_HOLD` and is old (2026-09-15), so it is NOT a current publication receipt.

### Historical producer-receipt coverage
Of 107 verified grade-linked pregame predictions, 42 snapshots include `calibration_operation_receipt` and `model_consumed_feature_hash`; the other 65 older snapshots (2026-10-01 through 2026-10-25 local) lack both keys. The 42 newer rows are dated 2026-10-26 through 2026-11-10 local. The existence of the keys alone is not validation of digest, numerical transform, or feature consumption. Older immutable predictions must not be rewritten; SIRT must determine whether historical pregame grades can contribute to G4 independently of newer G3 producer-receipt policy.

### Adversarial cohort checks
Decile 0.50–0.59: n=42, mean forecast 0.54867, actual hit rate 0.47619, Brier 0.25508, bias +0.07248. This is a watch/cohort challenge, not evidence for a probability weight change or a universal haircut.
Decile 0.90–0.99: n=1; not interpretable for calibration.

## Specific work to close
1. **SIRT independent replay:** execute the exact-version SQL from pinned SHA; independently reconstruct ECE from 10 bins, verify final outcomes/cutoffs and examine the 0.50–0.59 cohort, leakage risks and successful counterexamples.
2. **G3 historical/new split:** verify each of 42 producer receipts using `_actual_producer_receipt`; verify canonical current-slate scorer returns a producer receipt and fresh final requery. Do not fabricate the 65 missing old receipts.
3. **G4/G5 evidence ordering:** independently establish an authorized forward evidence-collection path without promoting a candidate simply to generate grades; keep certification/publication on hold until the precise proof and authority exist.
4. **G7 certification and runtime:** the protected independent reviewer (not Engineering or this audit) must approve artifact/calibration checksums, chronological evidence, model failure-path behavior and registry certification. After a governed promotion, verify actual Render SHA, authenticated Action and immutable pregame prediction; run final `V17_TERMINAL_REDUCER` acceptance.
5. **Cross-sport reuse:** only after NFL audit acceptance, adapt equivalent read-only proofs for NBA/WNBA replay and NHL authorized-goalie evidence; do not conflate unavailable inputs with unavailable fitted capability.

## Non-authorizing assertions
`research_status=WORKING_NOT_COMPLETE`
`automatic_promotion=false`
`audit_authorizes_certification=false`
`audit_authorizes_publication=false`
`can_execute=false`
No wagers, orders, registration, model weight changes or publication-flag edits.
