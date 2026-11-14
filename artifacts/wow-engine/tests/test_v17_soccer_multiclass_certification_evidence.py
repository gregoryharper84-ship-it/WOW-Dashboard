"""Regression checks for soccer 1X2 candidate-bound source and replay proofs."""
from __future__ import annotations

from copy import deepcopy

from v17.candidate_certification_evidence import _hash
from v17 import soccer_multiclass_certification_evidence as soccer


def _row():
    manifest = {
        "policy": soccer.SOURCE_POLICY, "license": "CC0-1.0", "competition": "EPL",
        "source_url": soccer.SOURCE_URL_PREFIX + "2025-26/en.1.json",
        "source_sha256": "a" * 64, "market_features_used": False,
        "outcome_space": ["HOME", "DRAW", "AWAY"],
    }
    return {
        "official_event_id": "EPL:fixture-one",
        "event_start_time": "2026-04-01T18:00:00+00:00",
        "feature_as_of": "2026-04-01T17:59:59+00:00",
        "outcome_json": {"outcome": "DRAW"},
        "features": {"home_recent_win_rate": 0.5},
        "source_manifest": manifest, "source_manifest_sha256": _hash(manifest),
        "market_features_used": False, "can_execute": False,
    }


def test_valid_source_uses_real_three_way_outcome_not_binary():
    value, blockers = soccer._validated_row(_row(), {"league": "EPL"}, ("home_recent_win_rate",))
    assert blockers == []
    assert value.outcome == "DRAW"
    assert value.event_id == "EPL:fixture-one"
    assert value.features["home_recent_win_rate"] == 0.5


def test_manifest_tamper_rejected_even_if_features_plausible():
    row = _row()
    row["source_manifest"]["license"] = "PROPRIETARY"
    value, blockers = soccer._validated_row(row, {"league": "EPL"}, ("home_recent_win_rate",))
    assert value is None
    assert "SOURCE_MANIFEST_INVALID" in blockers


def test_source_hash_alone_does_not_approve_wrong_competition_or_rights():
    row = _row()
    row["source_manifest"]["competition"] = "LALIGA"
    row["source_manifest_sha256"] = _hash(row["source_manifest"])
    _, blockers = soccer._validated_row(row, {"league": "EPL"}, ("home_recent_win_rate",))
    assert "SOURCE_MANIFEST_INVALID" in blockers


def test_temporal_leakage_is_blocked():
    row = _row()
    row["feature_as_of"] = row["event_start_time"]
    _, blockers = soccer._validated_row(row, {"league": "EPL"}, ("home_recent_win_rate",))
    assert "MULTICLASS_TEMPORAL_LEAKAGE" in blockers


def test_bad_outcomes_boolean_nonfinite_and_foreign_event_identity_are_rejected():
    variants = []
    a = _row()
    a["outcome_json"] = {"outcome": "HOME_OR_DRAW"}
    variants.append(a)
    b = _row()
    b["features"] = {"home_recent_win_rate": float("nan")}
    variants.append(b)
    c = _row()
    c["features"] = {"home_recent_win_rate": True}
    variants.append(c)
    d = _row()
    d["official_event_id"] = "SERIE_A:fixture-one"
    variants.append(d)
    for item in variants:
        value, blockers = soccer._validated_row(item, {"league": "EPL"}, ("home_recent_win_rate",))
        assert value is None
        assert "TRAINING_ROW_SHAPE_INVALID" in blockers


def test_research_verifier_is_never_an_execution_or_publication_authority():
    assert soccer.CAN_EXECUTE is False
    assert soccer.PROBABILITY_PUBLISHABLE is False


class _Query:
    def __init__(self, rows):
        self.data = rows

    def select(self, *_):
        return self

    def eq(self, *_):
        return self

    def limit(self, *_):
        return self

    def execute(self):
        return self


class _Database:
    def __init__(self, rows):
        self.rows = rows
        self.writes = 0

    def table(self, name):
        assert name == "wow_d1_candidate_artifacts"
        return _Query(self.rows)


def test_absent_candidate_blocks_without_any_receipt_write():
    db = _Database([])
    result = soccer.verify_soccer_candidate_certification_evidence(db, "absent")
    assert result["status"] == "CERTIFICATION_EVIDENCE_BLOCKED"
    assert result["code"] == "CANDIDATE_ARTIFACT_NOT_FOUND"
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False


def test_existing_authenticated_dispatch_reuses_soccer_verifier(monkeypatch):
    from v17 import candidate_certification_evidence as entry
    expected = {"status": "CERTIFICATION_EVIDENCE_FAILED", "can_execute": False,
                "probability_publishable": False}
    monkeypatch.setattr(soccer, "verify_soccer_candidate_certification_evidence",
                        lambda db, candidate_id: expected)
    db = _Database([{"sport": "SOCCER",
                     "model_family": "SOCCER_1X2_MULTINOMIAL_LOGIT_V1_EPL"}])
    assert entry.verify_candidate_certification_evidence(db, "candidate-id") == expected
