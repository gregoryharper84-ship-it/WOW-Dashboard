# WOW V17: pregame provenance and challenger intake — shadow only

**Status:** Class A/B research test scaffolding plus Class C model challenger hypothesis. **Not deployed.** No model probability, calibrator, terminal label, or production registry change. `can_execute=false`.

## What the real validation database shows (read-only audit on 2026-10-26)

- MLB `wow_event_predictions` + `wow_event_outcomes`: **1,440 settled prediction rows spanning 168 distinct games**. Repeated model receipts are NOT independent games.
- After pregame model timing, fresh material update, official winner, `calibration_status=PASS`, `final_refresh_status=PASS`, `terminal_label=FINAL_APPROVED`, rank/publish eligibility and one latest valid forecast per game: **32 distinct games**. Descriptive Brier **0.247340**; log loss **0.687840**. This is the existing V17 incumbent, NOT a challenger.
- The source manifest recorded for each of the 89 qualifying prediction rows was captured **after the model timestamp**, though before game start. Its cryptographic hash is useful for after-the-fact integrity but CANNOT prove the model's original feature vector.
- Those 89 prediction rows have 890 joined frozen-scoring-evidence rows with payload hashes, but the recorded evidence timestamps/retrieval/write timestamps are later than their model timestamps. This requires a separate source-time reconciliation; do not silently reinterpret those timestamps.
- `wow_d1_training_rows` has **350,352 rows overall**. The MLB dynamic-team-state subgroup has **19,322 rows representing 9,640 distinct games** across 2023–2026. All are labeled `historical_reconstruction=true`, `archived_pregame_snapshot=false`. An as-of timestamp such as kickoff minus one second is NOT proof of pregame capture.
- 2026 MLB D1 alone contains **12,111 rows representing 2,429 games**, so treating row count as independent sample count would inflate training evidence.
- D1 IDs use `MLB:<numeric>` while event ledger IDs use `<numeric>`. **Normalize only this documented prefix, and require exact event UTC start**. All 32 qualified MLB games have a reconstructed D1 match, and **zero** have a D1 archived-pregame-feature match.

These results describe the sampled validation database at inspection time, not a production deployment state.

## Built in this PR

- `free_forecasting_shadow.py`: opt-in CatBoost/AutoGluon classifier fitting; deterministic train/validation/holdout; Brier/log loss/ECE; optional Optuna/MLflow. Now requires exact one-row-per-event outcomes, explicit archived capture, non-reconstructed flag, **feature observation <= archived capture < event kickoff**, matching digest and evidence receipt, plus capture <= incumbent model timestamp when making a paired comparison.
- `pregame_provenance_gate.py`: deterministic inventory of D1 reconstruction, conflicting duplicate event outcomes/features, timestamp/lineage validity, and source/model alignment. Importantly, **it does not self-certify** an archived flag or SHA-256 hash supplied by a backfilled row.
- `provenance_inventory_cli.py`: offline aggregate report writer; source evidence stays outside Git.
- `d1_provenance_export.sql`: bounded, read-only historical MLB D1 export.
- `mlb_pregame_source_alignment.sql`: read-only source snapshot versus model timestamp reconciliation.
- `mlb_incumbent_baseline_export.sql` and `mlb_incumbent_baseline_audit.py`: read-only scored incumbent/outcome baseline (not a training source).

## Reproduction

1. Run the SQL **SELECT** of interest against your authorized *validation* database and export its result set to a private JSON array. Never commit exports, secrets or player-private records.
2. For D1 audit: `python artifacts/wow-engine/v17/experiments/provenance_inventory_cli.py --kind d1 --input /private/rows.json --output /tmp/wow-d1-audit.json`.
3. For source audit: `python artifacts/wow-engine/v17/experiments/provenance_inventory_cli.py --kind source --input /private/source_rows.json --output /tmp/wow-source-audit.json`.
4. For clean training data only: `python artifacts/wow-engine/v17/experiments/free_forecasting_shadow.py --input /private/verified_snapshot_training.json --train-end 2024-12-31T00:00:00Z --validation-end 2025-12-31T00:00:00Z --backend catboost --output /tmp/wow-shadow.json`.
5. Run: `python -m pytest -q artifacts/wow-engine/v17/experiments/test_free_forecasting_shadow.py artifacts/wow-engine/v17/experiments/test_pregame_provenance_gate.py`.

## Necessary future production engineering integration — NOT implemented here

1. At **real live ingestion**, archive canonical event identity and exact feature vector **before the fitted model invocation**, with a genuine provider observation/retrieval timestamp, independent source refs, source payload hashes, `captured_at` and immutable receipt ID.
2. Verify archive-to-model-input equality (`model_inputs_hash` or exact schema/vector digest), not simply pregame clocks. Preserve the scoring freeze when later lineup/market status refreshes produce a new source manifest.
3. Store the immutable capture in an append-only, governed service table/object store with restrictive read/write access and independent audit evidence; never publish raw credentials/data in this repository.
4. Attach official settled outcome to exact `official_event_id` and start; retain at most one pre-registered forecast per game for independent holdout metrics. If the same event appears in multiple models, each model's cohort must deduplicate separately.
5. Ensure feature provenance includes **real observation time, retrieval time, archive time, and model input time**, not a retrospectively assigned kickoff-minus-one-second field.
6. Only after a forward archive and hash-equality check passes, let a governed Class C challenger train/tune against a frozen earlier cohort and compare a separate untouched forward holdout. `V17_TERMINAL_REDUCER` alone remains terminal publication authority.

## Honest limitations

A matching feature SHA-256 proves bytes agree, not **when**, **by whom**, or **from which authoritative provider** the values were observed. The existing D1 corpus can support *exploratory retrospective research* in a separated workflow, but not this strict prospective-evidence proof. Current vetted holdout has 32 independent events and cannot establish reliable calibration gains. Do not enable automatic promotion or wagering; `can_execute=false` remains invariant.
