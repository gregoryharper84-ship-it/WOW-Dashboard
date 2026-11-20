# WOW V17 pregame evidence-chain implementation plan
Date: 2026-11-18 | Spec: docs/superpowers/specs/2026-11-18-pregame-evidence-chain-design.md
Incident: #2020 | Execution: native GitHub connector, isolated feature branch
All steps preserve V17_TERMINAL_REDUCER and can_execute=false.

## P0 / Task 1 — establish observed baseline
Owner: Betting Intelligence research; Engineering accepts.
- Read validation Supabase tables and relevant repo contracts (do not guess schemas).
- Record latest forward cohort and exact-version counts, artifact identity, source link, calibration state and missing certification.
- Acceptance: immutable notes with project ID, date and actual query results; no model lifecycle edits.
- Observed: 2026-11-18 NFL forward health 107/100 PASS (Brier .2196498, log loss .6266275); 107 verified grades are one model version; no current matching independent certification receipt.

## P0 / Task 2 — add smallest read-only chain audit
Owner: Engineering / AMRT; independent test reviewer: SIRT.
Files:
- artifacts/wow-engine/scripts/v17_nfl_pregame_evidence_chain_audit.sql
- artifacts/wow-engine/tests/test_v17_nfl_pregame_evidence_chain_audit.py
Interfaces: current real public.wow_nfl_forward_verified_grades, wow_nfl_event_predictions, wow_nfl_source_snapshots, wow_nfl_event_fitted_model_artifacts, wow_calibrators, wow_team_event_specialist_certifications and wow_nfl_forward_calibration_health.
- First create static regression tests ensuring all tables, exact-match timestamps/hashes and non-authorizing output.
- Run query read-only against validation database, compare exact version count and every missing-link count.
- Expected: version NFL_EVENT_LOGREG_PLATT_V1_5d603a873fbb, n=107, 0 for link/timing/calibrator gaps, certification receipt gaps=107; audit_authorizes_certification/publication and can_execute always false.
- Run targeted pytest and repo CI; require independent gate. A query execution is not a certified model run.

## P0 / Task 3 — close NFL exact-version certification evidence
Owner: NFL fitted specialist + Engineering; challenger: SIRT; independent acceptance: Independent Verification.
- Recompute exact-version metrics including ECE from frozen chronological predictions and official settlements; compare incumbent vs baselines and counterexamples.
- Investigate existing G4/G5 prospective-evidence ordering: no automatic artifact activation to bootstrap certification.
- Prove original feature lineage and actual numerical consumption with isolated perturbation tests, avoiding double-counted opponent/rest/news variables.
- Obtain governed certification decision, then separately validate Render deploy, authenticated Action receipt, late-news rerun and final-refresh output. No self-signoff.
- Acceptance: exact-version SHA/receipt, validated cutoffs, independent PASS, scorer output, fresh immutable prediction and V17 reduction.

## P1 / Task 4 — NBA/WNBA and NHL expansion
Owner: Engineering/AMRT with exact specialists, SIRT verification.
- NBA/WNBA: shadow replay, injury/lineup/role and rest, opponent stats, calibration lower bounds; no promotion until holdout/forward evidence.
- NHL: rights-approved data reingest, two-provider starter goalies, TOI/shift and opponent-model numerics, fitted artifact/calibrator proof.
- Acceptance per cohort: n, Brier, log loss, mean forecast vs actual, bias, lower-bound reliability, correct predictions preserved, exact prediction receipts.

## P2 / Task 5 — extend all-sport coverage
Owner: Betting Intelligence product acceptance + sport specialists.
- Inventory each exact market and fitted model family for all 13 sports; classify unsupported/missing input/uncertified/scorer errors distinctly.
- Use current official feeds and approved open resources first, zero added paid subscriptions.
- Accept only exact-once reconcilable candidate rows; no forcing qualified output.

## Review focus
- Hash presence alone does not demonstrate numerically consumed features.
- Source status CAPTURED is not a material personnel-status confirmation.
- Exact-version metrics must not be substituted with global mixed-version metrics.
- Historical graded predictions do not authorize current-slate publication.
- Missing independent certification must not be bypassed by a favorable audit.
- GitHub merge, Render deploy, live GPT Action and V17 reducer proof are distinct.
