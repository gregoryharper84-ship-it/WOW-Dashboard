"""Local deterministic negative controls for research-tool receipt preflight."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import csv
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research"))
import v17_calibration_research_receipt as r


def _fixture(tmp_path, rows=None):
    (tmp_path / "source.csv").write_text("genuine source bytes not asserted by fixture\n")
    (tmp_path / "artifact.json").write_text('{"candidate":"synthetic-test-only"}')
    base = datetime(2024, 9, 15, 19, tzinfo=timezone.utc)
    if rows is None:
        rows = []
        for i, split in enumerate(("HOLDOUT", "FORWARD")):
            kickoff = base + timedelta(days=365 * i)
            rows.append(dict(official_event_id=f"event-{i}", split=split,
                             probability=str((0.8, 0.3)[i]),
                             actual=str((1, 0)[i]),
                             forecast_issued_at=(kickoff-timedelta(days=1)).isoformat(),
                             event_start_at=kickoff.isoformat(),
                             label_available_at=(kickoff+timedelta(hours=3)).isoformat()))
    with (tmp_path / "predictions.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "official_event_id", "split", "probability", "actual",
            "forecast_issued_at", "event_start_at", "label_available_at"])
        writer.writeheader()
        writer.writerows(rows)
    digest = lambda name: sha256((tmp_path / name).read_bytes()).hexdigest()
    manifest = dict(sport="NHL", model_family="TEST_CANDIDATE_ONLY",
                    source_files=[dict(path="source.csv", sha256=digest("source.csv"))],
                    artifact=dict(path="artifact.json", sha256=digest("artifact.json")),
                    predictions=dict(path="predictions.csv", sha256=digest("predictions.csv")))
    file = tmp_path / "manifest.json"
    file.write_text(json.dumps(manifest))
    return file, rows


def test_complete_local_bytes_and_split_controls_are_not_certification(tmp_path):
    path, _ = _fixture(tmp_path)
    meta, rows = r.load_cohort(path)
    assert meta["can_execute"] is False
    assert meta["probability_publishable"] is False
    assert meta["model_certified"] is False
    assert meta["source_rights_independently_verified"] is False
    assert len(meta["artifact_sha256"]) == 64
    assert set(x["split"] for x in rows) == {"HOLDOUT", "FORWARD"}


def test_metrics_brier_bias_and_bins_deterministic(tmp_path):
    path, _ = _fixture(tmp_path)
    _, rows = r.load_cohort(path)
    m = r.metrics(rows)
    assert m["n"] == 2
    assert m["brier"] == pytest.approx(0.065)
    assert m["calibration_bias"] == pytest.approx(0.05)
    assert sum(x["n"] for x in m["reliability_bins"]) == 2
    assert m == r.metrics(rows)


def test_artifact_tampering_fails_before_metrics(tmp_path):
    path, _ = _fixture(tmp_path)
    (tmp_path / "artifact.json").write_text('{"candidate":"tampered"}')
    with pytest.raises(r.ResearchHold, match="RESEARCH_FILE_SHA256_MISMATCH"):
        r.load_cohort(path)


def test_source_tampering_fails_before_metrics(tmp_path):
    path, _ = _fixture(tmp_path)
    (tmp_path / "source.csv").write_text("tampered")
    with pytest.raises(r.ResearchHold, match="RESEARCH_FILE_SHA256_MISMATCH"):
        r.load_cohort(path)


def test_poststart_forecast_is_typed_hold(tmp_path):
    _, rows = _fixture(tmp_path)
    rows[0]["forecast_issued_at"] = rows[0]["event_start_at"]
    path, _ = _fixture(tmp_path, rows)
    with pytest.raises(r.ResearchHold, match="RESEARCH_CUTOFF_OR_SETTLEMENT_INVALID"):
        r.load_cohort(path)


def test_duplicate_event_identity_rejected_across_cohorts(tmp_path):
    _, rows = _fixture(tmp_path)
    rows[1]["official_event_id"] = rows[0]["official_event_id"]
    path, _ = _fixture(tmp_path, rows)
    with pytest.raises(r.ResearchHold, match="RESEARCH_EVENT_IDENTITY_MISSING_OR_DUPLICATE"):
        r.load_cohort(path)


def test_unsafe_probability_and_malformed_label_rejected(tmp_path):
    _, rows = _fixture(tmp_path)
    for bad in ("nan", "1.5", "-0.1"):
        candidate = [dict(row) for row in rows]
        candidate[0]["probability"] = bad
        path, _ = _fixture(tmp_path, candidate)
        with pytest.raises(r.ResearchHold, match="RESEARCH_PREDICTION_OR_LABEL_INVALID"):
            r.load_cohort(path)
    rows[0]["actual"] = "1.0"
    path, _ = _fixture(tmp_path, rows)
    with pytest.raises(r.ResearchHold, match="RESEARCH_PREDICTION_OR_LABEL_INVALID"):
        r.load_cohort(path)


def test_split_leakage_and_missing_forward_rejected(tmp_path):
    _, rows = _fixture(tmp_path)
    rows[0]["label_available_at"] = rows[1]["forecast_issued_at"]
    path, _ = _fixture(tmp_path, rows)
    with pytest.raises(r.ResearchHold, match="RESEARCH_LABEL_LEAKAGE"):
        r.load_cohort(path)
    rows[0]["label_available_at"] = rows[0]["event_start_at"]
    rows[1]["split"] = "HOLDOUT"
    path, _ = _fixture(tmp_path, rows)
    with pytest.raises(r.ResearchHold, match="RESEARCH_SPLIT_MISSING"):
        r.load_cohort(path)


def test_manifest_traversal_and_missing_source_fail_closed(tmp_path):
    path, _ = _fixture(tmp_path)
    manifest = json.loads(path.read_text())
    manifest["artifact"]["path"] = "../artifact.json"
    path.write_text(json.dumps(manifest))
    with pytest.raises(r.ResearchHold, match="RESEARCH_FILE_MANIFEST_INVALID"):
        r.load_cohort(path)
    manifest["artifact"]["path"] = "artifact.json"
    manifest["source_files"] = []
    path.write_text(json.dumps(manifest))
    with pytest.raises(r.ResearchHold, match="RESEARCH_SOURCE_SNAPSHOTS_MISSING"):
        r.load_cohort(path)


def test_external_tool_missing_must_not_create_completion_receipt(tmp_path, monkeypatch):
    path, _ = _fixture(tmp_path)
    import builtins
    original = builtins.__import__

    def offline_import(name, *args, **kwargs):
        if name == "mlflow":
            raise ImportError("forced missing dependency")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", offline_import)
    out = tmp_path / "out"
    with pytest.raises(r.ResearchHold, match="RESEARCH_MLFLOW_OR_EVIDENTLY_NOT_INSTALLED"):
        r.run(path, out, tmp_path / "mlruns")
    assert not (out / "research_tool_receipt.json").exists()


def test_no_synthetic_research_result_can_publish():
    assert r.CAN_EXECUTE is False
    assert r.PROBABILITY_PUBLISHABLE is False
    assert r.PROMOTION_AUTHORIZED is False
    assert r.TERMINAL_AUTHORITY == "V17_TERMINAL_REDUCER"


def test_reused_output_directory_cannot_upload_stale_receipt(tmp_path):
    manifest, _ = _fixture(tmp_path)
    out = tmp_path / "reports"
    out.mkdir()
    (out / "research_tool_receipt.json").write_text('{"old":true}')
    with pytest.raises(r.ResearchHold, match="RESEARCH_OUTPUT_ROOT_NOT_EMPTY"):
        r.ensure_safe_output_locations(manifest, out, tmp_path / "tracking")


def test_source_inside_output_and_recursive_tracking_rejected(tmp_path):
    manifest, _ = _fixture(tmp_path)
    with pytest.raises(r.ResearchHold, match="RESEARCH_OUTPUT_OVERLAPS_INPUT"):
        r.ensure_safe_output_locations(manifest, tmp_path, tmp_path.parent / "tracking")
    out = tmp_path / "fresh_reports"
    with pytest.raises(r.ResearchHold, match="RESEARCH_TRACKING_OVERLAPS_OUTPUT"):
        r.ensure_safe_output_locations(manifest, out, out / "mlruns")
    with pytest.raises(r.ResearchHold, match="RESEARCH_TRACKING_OVERLAPS_OUTPUT"):
        r.ensure_safe_output_locations(manifest, out / "reports", out)


def test_naive_issued_at_rejected_as_typed_hold(tmp_path):
    _, rows = _fixture(tmp_path)
    rows[0]["forecast_issued_at"] = "2024-09-14T19:00:00"
    manifest, _ = _fixture(tmp_path, rows)
    with pytest.raises(r.ResearchHold, match="RESEARCH_TIMESTAMP_INVALID"):
        r.load_cohort(manifest)


def test_malformed_input_schema_fails_closed(tmp_path):
    manifest, _ = _fixture(tmp_path)
    rows = list(csv.DictReader((tmp_path / "predictions.csv").open()))
    for row in rows:
        row.pop("actual")
    with (tmp_path / "predictions.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    data = json.loads(manifest.read_text())
    data["predictions"]["sha256"] = sha256((tmp_path / "predictions.csv").read_bytes()).hexdigest()
    manifest.write_text(json.dumps(data))
    with pytest.raises(r.ResearchHold, match="RESEARCH_PREDICTION_SCHEMA_INVALID"):
        r.load_cohort(manifest)
