from types import SimpleNamespace

from fastapi import FastAPI

import v17.candidate_certification_evidence as evidence
from v17.team_event_certification_replay import build_certification_report


def candidate(**overrides):
    row = {
        "candidate_id": "11111111-1111-1111-1111-111111111111",
        "created_at": "2026-09-26T00:00:00+00:00",
        "sport": "NCAAF",
        "league": "NCAAF",
        "model_family": "NCAAF_DYNAMIC_TEAM_STATE_LOGIT_V2",
        "model_artifact_version": "NCAAF_DYNAMIC_TEAM_STATE_LOGIT_V2_aaaaaaaaaaaaaaaa_bbbbbbbbbbbb",
        "training_dataset_hash": "a" * 64,
        "training_code_sha": "b" * 40,
        "artifact_checksum": "c" * 64,
        "training_rows": 1,
        "calibration_rows": 1,
        "test_rows": 1,
        "research_screen_pass": True,
        "source_review_status": "REQUIRED",
        "lifecycle_state": "CANDIDATE",
        "promoted": False,
        "active": False,
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "can_execute": False,
    }
    row.update(overrides)
    return row


def receipt(row, **overrides):
    value = {
        "candidate_id": row["candidate_id"],
        "model_artifact_version": row["model_artifact_version"],
        "training_dataset_hash": row["training_dataset_hash"],
        "artifact_checksum": row["artifact_checksum"],
        "source_review_status": "PASS",
        "replay_status": "PASS",
    }
    value.update(overrides)
    return value


def test_exact_receipt_is_candidate_bound_and_nonpublishing():
    row = candidate()
    report = build_certification_report([row], certification_evidence_receipts=[receipt(row)])
    ncaaf = next(item for item in report["sports"] if item["sport"] == "NCAAF")
    lane = ncaaf["lanes"][0]
    assert lane["status"] == "CERTIFICATION_REPLAY_PASS"
    assert lane["candidate_bound_evidence_pass"] is True
    assert lane["probability_publishable"] is False
    assert lane["can_execute"] is False


def test_stale_same_lane_receipt_does_not_count():
    row = candidate()
    stale = receipt(row, candidate_id="22222222-2222-2222-2222-222222222222")
    report = build_certification_report([row], certification_evidence_receipts=[stale])
    lane = next(item for item in report["sports"] if item["sport"] == "NCAAF")["lanes"][0]
    assert lane["status"] == "SOURCE_REVIEW_PENDING"
    assert lane["candidate_bound_evidence_pass"] is False
    assert "SOURCE_REVIEW_REQUIRED" in lane["blockers"]


def test_failed_replay_receipt_does_not_count():
    row = candidate()
    failed = receipt(row, replay_status="FAIL")
    report = build_certification_report([row], certification_evidence_receipts=[failed])
    lane = next(item for item in report["sports"] if item["sport"] == "NCAAF")["lanes"][0]
    assert lane["candidate_bound_evidence_pass"] is False


def test_evidence_route_install_is_idempotent():
    app = FastAPI()
    evidence.install_candidate_certification_evidence_route(app, auth_dependency=lambda: None, db_client_fn=lambda: None)
    evidence.install_candidate_certification_evidence_route(app, auth_dependency=lambda: None, db_client_fn=lambda: None)
    assert [route.path for route in app.router.routes].count(
        "/internal/v17/team-event-certification-evidence/{candidate_id}"
    ) == 1


def test_verifier_persists_pass_only_when_exact_replay_matches(monkeypatch):
    row = candidate(
        feature_schema_version="NCAAF_DYNAMIC_TEAM_STATE_FEATURES_V2",
        source_policy_id="TEAM_STATE_DYNAMIC_PRIOR_ONLY_V1",
        artifact_payload={"feature_names": ["x"]},
        calibrator_payload={"method": "IDENTITY_RAW_PROBABILITY_V1"},
        validation_metrics={"train_n": 1, "calibration_n": 1, "test_n": 1},
    )
    row["artifact_checksum"] = evidence._hash(row["artifact_payload"])
    manifest = {
        "market_features_used": False,
        "manual_probability_adjustments": False,
        "source_manifest": {"source": "CFBD:/games"},
    }
    persisted_rows = [
        {"official_event_id": "e1", "event_start_time": "2026-01-02T00:00:00+00:00", "feature_as_of": "2026-01-01T23:59:59+00:00", "features": {"x": 1.0}, "outcome_json": {"home_win": True}, "source_manifest": manifest, "source_manifest_sha256": evidence._hash(manifest), "market_features_used": False, "can_execute": False},
        {"official_event_id": "e2", "event_start_time": "2026-01-03T00:00:00+00:00", "feature_as_of": "2026-01-02T23:59:59+00:00", "features": {"x": 0.0}, "outcome_json": {"home_win": False}, "source_manifest": manifest, "source_manifest_sha256": evidence._hash(manifest), "market_features_used": False, "can_execute": False},
        {"official_event_id": "e3", "event_start_time": "2026-01-04T00:00:00+00:00", "feature_as_of": "2026-01-03T23:59:59+00:00", "features": {"x": 1.0}, "outcome_json": {"home_win": True}, "source_manifest": manifest, "source_manifest_sha256": evidence._hash(manifest), "market_features_used": False, "can_execute": False},
    ]
    monkeypatch.setattr(evidence, "_all_rows", lambda _db, _candidate: persisted_rows)
    monkeypatch.setenv("CFBD_API_KEY", "configured")
    replay = SimpleNamespace(
        dataset_hash=row["training_dataset_hash"],
        artifact_payload=row["artifact_payload"],
        calibrator_payload=row["calibrator_payload"],
        metrics=SimpleNamespace(train_n=1, calibration_n=1, test_n=1),
    )
    monkeypatch.setattr(evidence, "train_binary_candidate", lambda *_args, **_kwargs: replay)
    monkeypatch.setattr(evidence, "asdict", lambda value: dict(value.__dict__))

    class Query:
        def __init__(self, table): self.table, self.to_insert = table, None
        def select(self, *_a, **_k): return self
        def eq(self, *_a, **_k): return self
        def limit(self, *_a, **_k): return self
        def insert(self, value): self.to_insert = dict(value); return self
        def execute(self):
            if self.to_insert is not None:
                return SimpleNamespace(data=[{"receipt_id": "r1", **self.to_insert}])
            if self.table == "wow_d1_candidate_artifacts": return SimpleNamespace(data=[row])
            return SimpleNamespace(data=[])

    class DB:
        def table(self, name): return Query(name)

    result = evidence.verify_candidate_certification_evidence(DB(), row["candidate_id"])
    assert result["status"] == "CERTIFICATION_EVIDENCE_PASS"
    assert result["source_review_status"] == "PASS"
    assert result["replay_status"] == "PASS"
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False
