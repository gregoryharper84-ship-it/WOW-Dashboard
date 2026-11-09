"""Research-only chronological source audit acceptance tests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from v17.research_chronology_audit import audit_chronological_corpus


def _example(tmp_path: Path):
    baseline = datetime(2026, 1, 1, 11, tzinfo=timezone.utc)
    rows = []
    for n, split in enumerate(("TRAIN", "CALIBRATION", "HOLDOUT")):
        kickoff = baseline + timedelta(days=30 * n)
        source = f"source{n}.json"
        feature = f"feature{n}.json"
        source_bytes = f"source-{n}".encode()
        feature_bytes = f"feature-{n}".encode()
        (tmp_path / source).write_bytes(source_bytes)
        (tmp_path / feature).write_bytes(feature_bytes)
        iso = lambda dt: dt.isoformat()
        rows.append({
            "row_id": f"row-{n}", "sport": "NHL", "model_family": "NHL_TEST_FIT_V1",
            "market_family": "MONEYLINE", "official_event_id": f"official-event-{n}",
            "thesis_key": "HOME:FULL_GAME_INCL_OT_SO", "split": split,
            "source_snapshot_id": f"captured-{n}", "source_file": source,
            "source_payload_sha256": sha256(source_bytes).hexdigest(),
            "feature_file": feature,
            "feature_snapshot_sha256": sha256(feature_bytes).hexdigest(),
            "source_published_at": iso(kickoff - timedelta(hours=4)),
            "source_captured_at": iso(kickoff - timedelta(hours=3)),
            "feature_frozen_at": iso(kickoff - timedelta(hours=1)),
            "event_start_at": iso(kickoff),
            "label_available_at": iso(kickoff + timedelta(hours=3)),
        })
    return rows


def test_exact_bytes_and_chronology_pass_research_only(tmp_path):
    report = audit_chronological_corpus(_example(tmp_path), source_root=tmp_path)
    assert report["status"] == "RESEARCH_AUDIT_PASS"
    assert report["row_count"] == 3
    assert report["source_bytes_digest_verified"] is True
    assert [r["status"] for r in report["row_results"]] == ["ROW_CHECKS_PASS"] * 3
    assert report["probability_publishable"] is False
    assert report["fitted_model_certified"] is False
    assert report["source_timestamps_independently_verified"] is False
    assert report["can_execute"] is False
    assert report["calibrated_lower_bound"] is None


def test_rejects_postgame_freeze(tmp_path):
    rows = _example(tmp_path)
    rows[0]["feature_frozen_at"] = rows[0]["label_available_at"]
    report = audit_chronological_corpus(rows, source_root=tmp_path)
    assert "SOURCE_OR_FEATURE_CHRONOLOGY_INVALID" in report["blockers"]


def test_rejects_mismatched_actual_payload_bytes(tmp_path):
    rows = _example(tmp_path)
    (tmp_path / "feature1.json").write_bytes(b"tampered")
    report = audit_chronological_corpus(rows, source_root=tmp_path)
    assert "HASH_MISMATCH:feature_snapshot_sha256" in report["blockers"]
    assert report["source_bytes_digest_verified"] is False


def test_rejects_naive_timestamps(tmp_path):
    rows = _example(tmp_path)
    rows[0]["source_captured_at"] = "2026-01-01T08:00:00"
    report = audit_chronological_corpus(rows, source_root=tmp_path)
    assert "TIMESTAMP_INVALID_OR_NAIVE:source_captured_at" in report["blockers"]


def test_rejects_duplicate_and_cross_split_event(tmp_path):
    rows = _example(tmp_path)
    rows[1]["official_event_id"] = rows[0]["official_event_id"]
    rows[1]["thesis_key"] = rows[0]["thesis_key"]
    report = audit_chronological_corpus(rows, source_root=tmp_path)
    assert "EVENT_CROSSES_SPLITS" in report["blockers"]
    assert "DUPLICATE_THESIS" in report["blockers"]
    assert len(report["row_results"]) == 3


def test_rejects_train_label_known_after_calibration_cutoff(tmp_path):
    rows = _example(tmp_path)
    cutoff = datetime.fromisoformat(rows[1]["feature_frozen_at"])
    rows[0]["label_available_at"] = (cutoff + timedelta(minutes=1)).isoformat()
    report = audit_chronological_corpus(rows, source_root=tmp_path)
    assert "TRAIN_LABEL_LEAKS_INTO_CALIBRATION" in report["blockers"]


def test_rejects_ordered_split_overlap(tmp_path):
    rows = _example(tmp_path)
    # Calibrated sample predates training sample even though its own chronology holds.
    for field in (
        "source_published_at", "source_captured_at", "feature_frozen_at",
        "event_start_at", "label_available_at",
    ):
        rows[1][field] = (datetime.fromisoformat(rows[0][field]) - timedelta(days=10)).isoformat()
    report = audit_chronological_corpus(rows, source_root=tmp_path)
    assert "TRAIN_CALIBRATION_EVENT_CHRONOLOGY_OVERLAP" in report["blockers"]


def test_rejects_missing_split(tmp_path):
    rows = _example(tmp_path)[:2]
    report = audit_chronological_corpus(rows, source_root=tmp_path)
    assert "SPLIT_MISSING:HOLDOUT" in report["blockers"]


def test_rejects_path_traversal_and_symlink(tmp_path):
    rows = _example(tmp_path)
    rows[0]["source_file"] = "../source0.json"
    assert "FILE_PATH_OUTSIDE_ROOT:source_file" in audit_chronological_corpus(
        rows, source_root=tmp_path
    )["blockers"]
    outer = tmp_path.parent / f"{tmp_path.name}-outer.bin"
    outer.write_bytes(b"outside")
    (tmp_path / "escaped.bin").symlink_to(outer)
    rows[0]["source_file"] = "escaped.bin"
    assert "FILE_PATH_OUTSIDE_ROOT:source_file" in audit_chronological_corpus(
        rows, source_root=tmp_path
    )["blockers"]


def test_empty_or_malformed_rows_cannot_pass(tmp_path):
    assert "CORPUS_EMPTY" in audit_chronological_corpus([], source_root=tmp_path)["blockers"]
    report = audit_chronological_corpus([None, {}], source_root=tmp_path)
    assert report["row_count"] == len(report["row_results"]) == 2
    assert "ROW_NOT_OBJECT" in report["blockers"]


def test_deterministic_digest(tmp_path):
    rows = _example(tmp_path)
    one = audit_chronological_corpus(rows, source_root=tmp_path)
    two = audit_chronological_corpus(rows, source_root=tmp_path)
    assert one["input_metadata_sha256"] == two["input_metadata_sha256"]
    assert one["status"] == two["status"]


def test_timestamp_overflow_reports_typed_row_hold(tmp_path):
    rows = _example(tmp_path)
    rows[0]["source_published_at"] = "0001-01-01T00:00:00+05:00"
    report = audit_chronological_corpus(rows, source_root=tmp_path)
    assert report["status"] == "RESEARCH_AUDIT_HOLD"
    assert report["row_results"][0]["status"] == "ROW_HOLD"
    assert "TIMESTAMP_INVALID_OR_NAIVE:source_published_at" in report["blockers"]


def test_embedded_nul_path_gets_typed_hold_not_exception(tmp_path):
    rows = _example(tmp_path)
    rows[0]["source_file"] = "bad" + chr(0) + "file"
    report = audit_chronological_corpus(rows, source_root=tmp_path)
    assert report["status"] == "RESEARCH_AUDIT_HOLD"
    assert report["row_results"][0]["status"] == "ROW_HOLD"
    assert "FILE_UNREADABLE:source_file" in report["blockers"]


def test_duplicate_row_id_unknown_split_and_invalid_digest_hold(tmp_path):
    rows = _example(tmp_path)
    rows[1]["row_id"] = rows[0]["row_id"]
    rows[2]["split"] = "UNKNOWN"
    rows[0]["source_payload_sha256"] = "NOT-A-SHA256"
    report = audit_chronological_corpus(rows, source_root=tmp_path)
    assert report["status"] == "RESEARCH_AUDIT_HOLD"
    assert "DUPLICATE_ROW_ID" in report["blockers"]
    assert "SPLIT_UNKNOWN" in report["blockers"]
    assert "HASH_INVALID:source_payload_sha256" in report["blockers"]


def test_calibration_label_after_holdout_freeze_blocks(tmp_path):
    rows = _example(tmp_path)
    freeze = datetime.fromisoformat(rows[2]["feature_frozen_at"])
    rows[1]["label_available_at"] = (freeze + timedelta(minutes=1)).isoformat()
    report = audit_chronological_corpus(rows, source_root=tmp_path)
    assert report["status"] == "RESEARCH_AUDIT_HOLD"
    assert "CALIBRATION_LABEL_LEAKS_INTO_HOLDOUT" in report["blockers"]


def test_calibration_holdout_event_overlap_blocks(tmp_path):
    rows = _example(tmp_path)
    shift = timedelta(days=40)
    for field in (
        "source_published_at", "source_captured_at", "feature_frozen_at",
        "event_start_at", "label_available_at",
    ):
        rows[2][field] = (datetime.fromisoformat(rows[2][field]) - shift).isoformat()
    report = audit_chronological_corpus(rows, source_root=tmp_path)
    assert report["status"] == "RESEARCH_AUDIT_HOLD"
    assert "CALIBRATION_HOLDOUT_EVENT_CHRONOLOGY_OVERLAP" in report["blockers"]
