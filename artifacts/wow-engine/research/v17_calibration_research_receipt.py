"""Fail-closed, read-only MLflow + Evidently receipts for *existing* research predictions.

This consumes previously generated predictions. It cannot fit or score a model,
promote a candidate, publish a probability, change a terminal label, or wager.
A local digest/chronology check is not independent proof of source rights or
genuinely pregame issuance. Neither tool may be treated as a certification gate.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
import re

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
PROMOTION_AUTHORIZED = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
SHA = re.compile(r"^[0-9a-f]{64}$")
FIELDS = frozenset(("official_event_id", "split", "probability", "actual",
                    "forecast_issued_at", "event_start_at", "label_available_at"))


class ResearchHold(ValueError):
    """Invalid or incomplete evidence; never substitute fitted estimates."""


def _verified_file(root: Path, entry: dict) -> tuple[Path, str]:
    if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
        raise ResearchHold("RESEARCH_FILE_MANIFEST_INVALID")
    rel = Path(entry["path"])
    expected = entry.get("sha256")
    if rel.is_absolute() or ".." in rel.parts or not isinstance(expected, str) or not SHA.fullmatch(expected):
        raise ResearchHold("RESEARCH_FILE_MANIFEST_INVALID")
    try:
        path = (root / rel).resolve(strict=True)
        if not path.is_relative_to(root) or not path.is_file():
            raise ResearchHold("RESEARCH_FILE_OUTSIDE_ROOT")
        digest = sha256(path.read_bytes()).hexdigest()
    except (OSError, ValueError, RuntimeError):
        raise ResearchHold("RESEARCH_FILE_UNREADABLE") from None
    if digest != expected:
        raise ResearchHold("RESEARCH_FILE_SHA256_MISMATCH")
    return path, digest


def _dt(value: object) -> datetime:
    if not isinstance(value, str):
        raise ResearchHold("RESEARCH_TIMESTAMP_INVALID")
    try:
        d = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if d.tzinfo is None or d.utcoffset() is None:
            raise ValueError("naive timestamp")
        return d.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise ResearchHold("RESEARCH_TIMESTAMP_INVALID") from None


def load_cohort(manifest_path: Path) -> tuple[dict, list[dict]]:
    """Require real files/hashes and chronological, disjoint settled cohorts."""
    root = manifest_path.resolve().parent
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        raise ResearchHold("RESEARCH_MANIFEST_UNREADABLE") from None
    if not isinstance(manifest, dict) or not manifest.get("sport") or not manifest.get("model_family"):
        raise ResearchHold("RESEARCH_MANIFEST_INVALID")
    if not isinstance(manifest.get("source_files"), list) or not manifest["source_files"]:
        raise ResearchHold("RESEARCH_SOURCE_SNAPSHOTS_MISSING")
    checked = {}
    for name, item in (("artifact", manifest.get("artifact")),
                       ("predictions", manifest.get("predictions"))):
        checked[name] = _verified_file(root, item)
    sources = [_verified_file(root, x) for x in manifest["source_files"]]
    if len({p.resolve() for p, _ in sources}) != len(sources):
        raise ResearchHold("RESEARCH_SOURCE_SNAPSHOT_DUPLICATE")
    try:
        with checked["predictions"][0].open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames or not FIELDS.issubset(reader.fieldnames):
                raise ResearchHold("RESEARCH_PREDICTION_SCHEMA_INVALID")
            records = list(reader)
    except (OSError, UnicodeError, csv.Error):
        raise ResearchHold("RESEARCH_PREDICTIONS_UNREADABLE") from None
    if not records:
        raise ResearchHold("RESEARCH_COHORT_EMPTY")
    keys = set()
    splits = set()
    result = []
    for record in records:
        event = str(record["official_event_id"]).strip()
        split = str(record["split"]).strip().upper()
        if not event or event in keys:
            raise ResearchHold("RESEARCH_EVENT_IDENTITY_MISSING_OR_DUPLICATE")
        keys.add(event)
        if split not in ("HOLDOUT", "FORWARD"):
            raise ResearchHold("RESEARCH_SPLIT_UNKNOWN")
        splits.add(split)
        try:
            p = float(record["probability"])
            actual = int(record["actual"])
        except (TypeError, ValueError, OverflowError):
            raise ResearchHold("RESEARCH_PREDICTION_OR_LABEL_INVALID") from None
        if not math.isfinite(p) or not 0 <= p <= 1 or actual not in (0, 1) or str(record["actual"]) not in ("0", "1"):
            raise ResearchHold("RESEARCH_PREDICTION_OR_LABEL_INVALID")
        issued, started, label = (_dt(record[k]) for k in ("forecast_issued_at", "event_start_at", "label_available_at"))
        if not issued < started <= label:
            raise ResearchHold("RESEARCH_CUTOFF_OR_SETTLEMENT_INVALID")
        result.append(dict(official_event_id=event, split=split,
                           probability=p, actual=actual, issued=issued,
                           started=started, label=label))
    if splits != {"HOLDOUT", "FORWARD"}:
        raise ResearchHold("RESEARCH_SPLIT_MISSING")
    holdout = [r for r in result if r["split"] == "HOLDOUT"]
    forward = [r for r in result if r["split"] == "FORWARD"]
    if max(r["started"] for r in holdout) >= min(r["started"] for r in forward):
        raise ResearchHold("RESEARCH_SPLIT_CHRONOLOGY_OVERLAP")
    if max(r["label"] for r in holdout) >= min(r["issued"] for r in forward):
        raise ResearchHold("RESEARCH_LABEL_LEAKAGE")
    meta = dict(sport=manifest["sport"], model_family=manifest["model_family"],
                artifact_sha256=checked["artifact"][1],
                predictions_sha256=checked["predictions"][1],
                source_sha256={str(p.relative_to(root)): h for p, h in sources},
                can_execute=False, probability_publishable=False,
                source_rights_independently_verified=False,
                source_capture_times_independently_verified=False,
                model_certified=False)
    return meta, result


def metrics(rows: list[dict]) -> dict:
    if not rows:
        raise ResearchHold("RESEARCH_COHORT_EMPTY")
    n = len(rows)
    probs = [r["probability"] for r in rows]
    actual = [r["actual"] for r in rows]
    eps = 1e-12
    brier = sum((p-y)**2 for p, y in zip(probs, actual)) / n
    loss = -sum(y*math.log(max(eps,p))+(1-y)*math.log(max(eps,1-p))
                for p, y in zip(probs, actual)) / n
    bins = []
    for i in range(10):
        sample = [(p,y) for p,y in zip(probs,actual)
                  if i/10 <= p < (i+1)/10 or (i==9 and p==1)]
        if sample:
            count = len(sample)
            bins.append(dict(bin_index=i, n=count,
                             mean_prediction=sum(p for p,_ in sample)/count,
                             observed_rate=sum(y for _,y in sample)/count))
    ece = sum(x["n"]/n*abs(x["mean_prediction"]-x["observed_rate"]) for x in bins)
    return dict(n=n, brier=brier, log_loss=loss,
                calibration_bias=sum(probs)/n-sum(actual)/n,
                ece_10bin=ece, reliability_bins=bins)


def ensure_safe_output_locations(manifest_path: Path, out: Path, tracking_root: Path) -> None:
    """Prevent stale receipt reuse, input destruction and local artifact recursion."""
    source_root = manifest_path.resolve().parent
    output, tracking = out.resolve(), tracking_root.resolve()
    if output == source_root or output == tracking:
        raise ResearchHold("RESEARCH_OUTPUT_ROOT_INVALID")
    if tracking.is_relative_to(output) or output.is_relative_to(tracking):
        raise ResearchHold("RESEARCH_TRACKING_OVERLAPS_OUTPUT")
    # load_cohort already verified the manifest and all referenced paths.
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = [manifest["artifact"], manifest["predictions"], *manifest["source_files"]]
    inputs = [manifest_path.resolve()] + [
        (source_root / item["path"]).resolve() for item in entries
    ]
    if any(path == output or path.is_relative_to(output) for path in inputs):
        raise ResearchHold("RESEARCH_OUTPUT_OVERLAPS_INPUT")
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ResearchHold("RESEARCH_OUTPUT_ROOT_NOT_EMPTY")


def run(manifest_path: Path, out: Path, tracking_root: Path) -> dict:
    """Strict tool availability: fail before writing a misleading completion receipt."""
    meta, rows = load_cohort(manifest_path)
    ensure_safe_output_locations(manifest_path, out, tracking_root)
    try:
        import pandas as pd
        import mlflow
        from evidently import Report, Dataset, DataDefinition, BinaryClassification
        from evidently.presets import ClassificationQuality, DataDriftPreset
    except ImportError:
        raise ResearchHold("RESEARCH_MLFLOW_OR_EVIDENTLY_NOT_INSTALLED") from None
    by_split = {split: [r for r in rows if r["split"] == split]
                for split in ("HOLDOUT", "FORWARD")}
    summary = {split.lower(): metrics(part) for split, part in by_split.items()}
    frame = lambda part: pd.DataFrame([
        dict(official_event_id=r["official_event_id"],
             probability=r["probability"], actual=r["actual"])
        for r in part])
    definition = DataDefinition(classification=[
        BinaryClassification(target="actual", prediction_probas="probability")
    ])
    reference = Dataset.from_pandas(frame(by_split["HOLDOUT"]), data_definition=definition)
    current = Dataset.from_pandas(frame(by_split["FORWARD"]), data_definition=definition)
    snapshot = Report([ClassificationQuality(), DataDriftPreset(columns=["probability"])],
                      include_tests=False).run(current, reference)
    out.mkdir(parents=True, exist_ok=True)
    snapshot.save_html(str(out / "evidently_classification_and_drift.html"))
    payload = snapshot.json()
    (out / "evidently_classification_and_drift.json").write_text(
        payload if isinstance(payload, str) else json.dumps(payload, default=str))
    (out / "calibration_metrics.json").write_text(
        json.dumps(dict(metadata=meta, cohorts=summary, status="RESEARCH_ONLY_UNCERTIFIED"),
                   indent=2, sort_keys=True))
    tracking_root.mkdir(parents=True, exist_ok=True)
    mlflow.set_tracking_uri(tracking_root.resolve().as_uri())
    mlflow.set_experiment("wow_v17_research_calibration")
    with mlflow.start_run(run_name=str(meta["model_family"])) as active:
        mlflow.log_params(dict(sport=str(meta["sport"]), model_family=str(meta["model_family"]),
                               artifact_sha256=meta["artifact_sha256"],
                               predictions_sha256=meta["predictions_sha256"]))
        for cohort, data in summary.items():
            for name in ("n", "brier", "log_loss", "calibration_bias", "ece_10bin"):
                mlflow.log_metric(f"{cohort}_{name}", data[name])
        mlflow.log_artifacts(str(out), artifact_path="research_reports")
        run_id = active.info.run_id
    receipt = dict(status="RESEARCH_TOOL_RECEIPT_ONLY", mlflow_run_id=run_id,
                   mlflow_tracking_uri=tracking_root.resolve().as_uri(),
                   evidently_html_sha256=sha256((out / "evidently_classification_and_drift.html").read_bytes()).hexdigest(),
                   evidently_json_sha256=sha256((out / "evidently_classification_and_drift.json").read_bytes()).hexdigest(),
                   metadata=meta, cohorts=summary,
                   independent_acceptance=False, can_execute=False,
                   probability_publishable=False, terminal_authority=TERMINAL_AUTHORITY)
    (out / "research_tool_receipt.json").write_text(json.dumps(receipt, indent=2, sort_keys=True))
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--mlflow-store", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.manifest, args.output, args.mlflow_store),
                     indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
