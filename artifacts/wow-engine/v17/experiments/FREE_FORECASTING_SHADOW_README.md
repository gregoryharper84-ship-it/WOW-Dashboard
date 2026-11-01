# WOW V17 — Free Forecasting Challenger (research-only)

**State:** experimental source package, **not installed into Render**, not a production fitted specialist, and not an approval to publish probabilities. **Class C challenger research**, with tooling/test scaffolding only. `can_execute=false`; terminal authority remains `V17_TERMINAL_REDUCER`. This does **not** repair `MODEL_UNAVAILABLE` by itself.

This extends existing research infrastructure rather than replacing it. WOW already has `artifacts/wow-engine/requirements-research.txt` (DuckDB, Optuna, MLflow, Evidently) and the guarded `.github/workflows/wow-v17-model-improvement-experiment.yml`. CatBoost and AutoGluon are **optional** candidate trainers; no training library is imported by the scoring/publishing runtime. PyMC is reserved for a *separate*, stat-distribution challenger once an exact sport/stat dataset and model contract exist; installing it alone will not create valid prop probabilities.

## Optional dependencies (separate venv; NOT for the production Render service)

```bash
python -m venv .venv-wow-forecasting
. .venv-wow-forecasting/bin/activate
python -m pip install -r artifacts/wow-engine/requirements-research.txt
python -m pip install -r artifacts/wow-engine/v17/experiments/free_forecasting_optional_requirements.txt
```

The optional dependencies can be computationally heavy. Install individually if hardware cannot support AutoGluon. The deterministic gate/metric tests run under baseline pytest without any of these optional packages.

## Required dataset input (trusted, frozen, pregame-only)

A JSON **list** of already-settled, immutably matched binary prediction records for **one exact sport + market family**. Each row contains:

- `prediction_id`, `official_event_id`, `sport`, `market` — canonical immutable IDs. Never transform a materially different line or direction into the same record.
- `event_start_utc`, `features_asof_utc`, `outcome_available_utc` — timezone-aware timestamps. The upstream evidence freeze must certify the **latest** timestamp of every component feature.
- `outcome` — exact settled result (0 or 1), after official settlement; void/push/unknown rows are excluded upstream and must be accounted for separately.
- `features` — same set of numeric, finite **pregame** feature names and values in each row. No target, postgame score, late injury, closing-market outcome, hindsight context, or leaked label may be present. Source provenance is an upstream prerequisite; a claimed timestamp alone cannot prove it.
- Optional: `incumbent_probability` and `incumbent_model_timestamp_utc` — immutable, calibrated, truly pregame V17 champion probability for an **exact matching prediction**. If the holdout lacks a complete matched incumbent, the report does not claim a comparative result.

Do **not** fabricate historical records, attach sportsbook implied odds as `incumbent_probability`, or mix team-winner and player-prop thresholds. This experiment produces only an aggregate metric comparison; it never publishes row-level picks or model probabilities.

## Run

```bash
python artifacts/wow-engine/v17/experiments/free_forecasting_shadow.py \
  --input /path/to/approved_frozen_pregame_records.json \
  --train-end 2024-12-31T00:00:00Z \
  --validation-end 2025-12-31T00:00:00Z \
  --backend catboost --optuna-trials 0 \
  --output /tmp/wow-v17-free-forecasting-report.json
```

`--backend autogluon` invokes AutoGluon; `--optuna-trials 1..20` is available only for CatBoost. `--mlflow-local` writes **aggregate metrics only** to an isolated local MLflow file store beside the output; no online tracking by default. All time boundaries are caller-specified; chronological partitions are never randomized. Validation is used for tuning/early stopping, and the holdout is not used to select features or hyperparameters.

The gate rejects duplicated IDs, mixed market families, untrusted temporal order, mismatched feature schemas, invalid probabilities, outcomes unavailable before the next cohort's features, and insufficient splits. A model's favorable *holdout* score is **not permission to promote**: an independent holdout or forward cohort, calibration and lower-bound certification, feature provenance, counterexamples, sample-size assessment, and Class C governed review remain mandatory. Never retry optimization on the held-out period until it looks favorable.

Run the gate tests:

```bash
python -m pytest -q artifacts/wow-engine/v17/experiments/test_free_forecasting_shadow.py
```

## Acceptance and next increments

1. Initial: deterministic chronological/data-leakage and metric tests pass; CatBoost and AutoGluon paths remain opt-in; no production import, dependency or model-registry edits.
2. Connect a *real* immutable NFL LLP or MLB binary cohort through the existing internal evidence ledger. Do not broaden to multiple sports/markets in one fitted model by convenience.
3. Independently verify feature provenance and correct player/team status at freeze time; preserve typed failures and row reconciliation.
4. Run controlled training/calibration/holdout with reproducible hashes; compare paired Brier score, log loss, ECE and calibration bias against the genuine incumbent, with uncertainty/sample-size caveats. Do not fabricate a lower bound from these metrics.
5. Add a **separate** PyMC candidate for discrete stat distributions only when a supported fitted sport/stat specialist and settled dataset exist. Class C certification and `V17_TERMINAL_REDUCER` control all future promotion.

**No live wagering, trading, automatic certification, or deployment.**

## Evidence requirements added in the provenance hardening

The shadow trainer now **rejects** any row without these fields:

- `feature_evidence_kind="ARCHIVED_PREGAME_CAPTURE"`
- `historical_reconstruction=false`
- `feature_snapshot_captured_at_utc` (must occur at or after feature observation and before kickoff)
- `feature_evidence_receipt_id` identifying a separately maintained trusted immutable archive
- `feature_snapshot_sha256` of the canonical numeric feature map (must match)
- For a paired comparison, candidate capture time cannot be later than the incumbent model timestamp.

These validation fields are not independent cryptographic proof of *who* made the capture or *when* a historical source was truly available. The upstream archive's actual creation metadata, source provenance, and the model's exact consumed inputs must be independently reconciled before certification.

The `forward_feature_capture.py` helper produces a **write-once local research envelope** that explicitly requires separate source-verification; it does not by itself certify a feature vector and is not yet wired to the production scorer. Read `PREGAME_PROVENANCE_AUDIT.md` for the observed database chronology and next safe integration requirements.

