# V17 research-only MLflow + Evidently receipts

Incident: #2536

The research reporter does not fit models, invent predictions, publish probabilities, alter certification, touch production or execute orders. It consumes already-generated, settled candidate predictions. All outcomes retain RESEARCH_TOOL_RECEIPT_ONLY, can_execute=false and probability_publishable=false.

## Required inputs

Provide a genuinely acquired historical/forward source archive, exact candidate artifact, and **existing** event-level candidate predictions CSV with the following columns:

    official_event_id,split,probability,actual,forecast_issued_at,event_start_at,label_available_at

Split is HOLDOUT or FORWARD. Actual is binary 0/1; probability is the research model's pre-existing score, never invented or reconstructed from sportsbook odds. Timestamps are timezone-aware ISO-8601 and must satisfy forecast issuance < game start <= label availability. No event identity can appear twice or cross cohorts. The previous holdout labels must already be available before forward forecast issuance.

Create a JSON manifest alongside those files, with root-relative paths and real SHA256 digests:

    {
      "sport": "NHL",
      "model_family": "EXACT_CANDIDATE_ID",
      "artifact": {"path": "artifact.json", "sha256": "ACTUAL_64_HEX_DIGEST"},
      "predictions": {"path": "predictions.csv", "sha256": "ACTUAL_64_HEX_DIGEST"},
      "source_files": [
        {"path": "archive/source.csv", "sha256": "ACTUAL_64_HEX_DIGEST"}
      ]
    }

The examples are NOT completed evidence. The manifest must reference actual bytes of the model inputs and artifact.

## Offline run, no paid service

The already-declared research dependency set provides MLflow 3 and Evidently 0.7. Install outside the Render production environment:

    python -m pip install -r artifacts/wow-engine/requirements-research.txt
    python artifacts/wow-engine/research/v17_calibration_research_receipt.py       /path/to/workspace/manifest.json /path/to/experiment/reports       --mlflow-store /path/to/experiment/mlruns

The CLI requires an empty, separate report directory and a non-overlapping local MLflow store. After input validation, it writes the Evidently reports and calibration metrics before attempting the MLflow run. A failed MLflow call may leave partial reports but **cannot produce a successful research_tool_receipt.json**. Successful runs produce:

- calibration_metrics.json: cohort Brier, log loss, calibration bias, 10-bin ECE and reliability bins.
- evidently_classification_and_drift.html and .json: a genuine Evidently snapshot of classification quality and probability distribution drift.
- local MLflow run: exact artifact and input digests, metrics and report artifacts.
- research_tool_receipt.json: actual MLflow run ID and Evidently output digests.

A passing file-hash check is not independent evidence of source license, original pregame availability, model certification or fit quality. Evidently drift is diagnostic; the reporter is not a fitted calibrator or production publication gate. No betting or live execution is possible.

## Closure contract

The negative tests are synthetic **tests**, never operational evidence. To close the MLflow and Evidently checklist items, execute on an authorized real cohort, verify the local MLflow run and Evidently artifacts by readback, review cohort identity and chronology against acquisition receipts, and obtain independent verification. Current repository CI does not by itself achieve that closure.

SportsDataverse source and underlying upstream rights require review; CFBD needs authorized live credential/sample receipts; NOAA/Open-Meteo need archived as-of provenance and exact settlement reconciliation. Kaggle is deferred until a real compute limit is demonstrated.
