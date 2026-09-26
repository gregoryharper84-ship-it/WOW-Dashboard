from types import SimpleNamespace

from fastapi import FastAPI

import v17.candidate_certification_evidence as evidence
from v17.team_event_certification_replay import build_certification_report


def _candidate(**overrides):
    row = {
        "candidate_id": "11111111-1111-1111-1111-111111111111",
        "created_at": "2026-09-26T00:00:00+00:00",
        "sport": "NCAAF",
        "league": "NCAAF",
        "model_family": "NCAAF_DYNAMIC_TEAM_STATE_LOGIT_V2",
        "model_artifact_version": "NCAAF_DYNAMIC_TEAM_STATE_LOGIT_V2_aaaaaaaaaaaaaaaa_bbbbbbbbbbbb",
        "feature_schema_version": "NCAAF_DYNAMIC_TEAM_STATE_FEATURES_V2",
        "source_policy_id": "TEAM_STATE_DYNAMIC_PRIOR_ONLY_V1",
        "training_dataset_hash": "a" * 64,
        "training_code_sha": "b" * 40,
        "artifact_checksum": "c" * 64,
        "artifact_payload": {"feature_names": ["x"], "x": 1},
        "calibrator_payload": {"method": "IDENTITY_RAW_PROBABILITY_V1"},
        "validation_metrics": {"train_n": 1, "calibration_n": 1, "test_n": 1, "raw_brier": 0.1},
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


def _receipt(candidate, **overrides):
    row = {
        "candidate_id": candidate["candidate_id"],
        "model_artifact_version": candidate["model_artifact_version"],
        "training_dataset_hash": candidate["training_dataset_hash"],
        "artifact_checksum": candidate["artifact_checksum"],
        "source_review_status": "PASS",
        "replay_status": "PASS",
    }
    row.update(overrides)
    return row


def test_exact_candidate_receipt_clears_source_and_replay_blockers_only():
    candidate = _candidate()
    report = build_certification_report([candidate], certification_evidence_receipts=[_receipt(candidate)])
    ncaaf = next(row for row in report["sports"] if row["sport"] == "NCAAF")
    assert ncaaf["status"] == "CERTIFICATION_REPLAY_PASS"
    assert ncaaf["candidate_bound_evidence_pass_lane_count"] == 1
    lane = ncaaf["lanes"][0]
    assert lane["candidate_bound_evidence_pass"] is True
    assert lane["probability_publishable"] is False
    assert lane["can_execute"] is False


def test_same_lane_stale_or_mismatched_receipt_does_not_count():
    candidate = _candidate()
    stale = _receipt(candidate, candidate_id="22222222-2222-2222-2222-222222222222")
    report = build_certification_report([candidate], certification_evidence_receipts=[stale])
    ncaaf = next(row for row in report["sports"] if row["sport"] == "NCAAF")
    lane = ncaaf["lanes"][0]
    assert lane["status"] == "SOURCE_REVIEW_PENDING"
    assert lane["candidate_bound_evidence_pass"] is False
    assert "SOURCE_REVIEW_REQUIRED" in lane["blockers"]
    assert "PROSPECTIVE_OR_CERTIFICATION_REPLAY_EVIDENCE_REQUIRED" in lane["blockers"]


def test_fail_receipt_does_not_count():
    candidate = _candidate()
    failed = _receipt(candidate, replay_status="FAIL")
    report = build_certification_report([candidate], certification_evidence_receipts=[failed])
    lane = next(row for row in report["sports"] if row["sport"] == "NCAAF")["lanes"][0]
    assert lane["candidate_bound_evidence_pass"] is False
    assert lane["probability_publishable"] is False


class _Query:
    def __init__(self, db, table):
        self.db = db
        self.table_name = table
        self.insert_row = None
    def select(self, *_args, **_kwargs): return self
    def eq(self, *_args, **_kwargs): return self
    def limit(self, *_args, **_kwargs): return self
    def insert(self, row): self.insert_row = dict(row); return self
    def execute(self):
        if self.insert_row is not None:
            row = {"receipt_id": "r1", **self.insert_row}
            self.db.receipts.append(row)
            return SimpleNamespace(data=[row])
        if self.table_name == "wow_d1_candidate_artifacts":
            return SimpleNamespace(data=[self.db.candidate])
        if self.table_name == evidence.RECEIPT_TABLE:
            return SimpleNamespace(data=[])
        return SimpleNamespace(data=[])


class _DB:
    def __init__(self, candidate):
        self.candidate = candidate
        self.receipts = []
    def table(self, name): return _Query(self, name)


def test_verifier_persists_inert_pass_receipt(monkeypatch):
    candidate = _candidate()
    candidate["validation_metrics"] = {"train_n": 1, "calibration_n": 1, "test_n": 1, "raw_brier": 0.1}
    manifest = {
        "market_features_used": False,
        "manual_probability_adjustments": False,
        "source_manifest": {"source": "CFBD:/games"},
    }
    rows = [{
        "official_event_id": "e1",
        "event_start_time": "2026-01-02T00:00:00+00:00",
        "feature_as_of": "2026-01-01T23:59:59+00:00",
        "features": {"x": 1.0},
        "outcome_json": {"home_win": True},
        "source_manifest": manifest,
        "source_manifest_sha256": evidence._hash(manifest),
        "market_features_used": False,
        "can_execute": False,
    }, {
        "official_event_id": "e2",
        "event_start_time": "2026-01-03T00:00:00+00:00",
        "feature_as_of": "2026-01-02T23:59:59+00:00",
        "features": {"x": 0.0},
        "outcome_json": {"home_win": False},
        "source_manifest": manifest,
        "source_manifest_sha256": evidence._hash(manifest),
        "market_features_used": False,
        "can_execute": False,
    }, {
        "official_event_id": "e3",
        "event_start_time": "2026-01-04T00:00:00+00:00",
        "feature_as_of": "2026-01-03T23:59:59+00:00",
        "features": {"x": 1.0},
        "outcome_json": {"home_win": True},
        "source_manifest": manifest,
        "source_manifest_sha256": evidence._hash(manifest),
        "market_features_used": False,
        "can_execute": False,
    }]
    monkeypatch.setattr(evidence, "_all_rows", lambda _db, _candidate: rows)
    monkeypatch.setenv("CFBD_API_KEY", "configured")
    replay = SimpleNamespace(
        dataset_hash=candidate["training_dataset_hash"],
        artifact_payload=candidate["artifact_payload"],
        calibrator_payload=candidate["calibrator_payload"],
        metrics=SimpleNamespace(train_n=1, calibration_n=1, test_n=1, raw_brier=0.1),
    )
    monkeypatch.setattr(evidence, "train_binary_candidate", lambda *_args, **_kwargs: replay)
    monkeypatch.setattr(evidence, "asdict", lambda value: dict(value.__dict__))
    candidate["artifact_checksum"] = evidence._hash(candidate["artifact_payload"])
    db = _DB(candidate)
    result = evidence.verify_candidate_certification_evidence(db, candidate["candidate_id"])
    assert result["status"] == "CERTIFICATION_EVIDENCE_PASS"
    assert result["source_review_status"] == "PASS"
    assert result["replay_status"] == "PASS"
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False
    assert db.receipts[0]["candidate_id"] == candidate["candidate_id"]


def test_evidence_route_install_is_idempotent():
    app = FastAPI()
    evidence.install_candidate_certification_evidence_route(app, auth_dependency=lambda: None, db_client_fn=lambda: _DB(_candidate()))
    evidence.install_candidate_certification_evidence_route(app, auth_dependency=lambda: None, db_client_fn=lambda: _DB(_candidate()))
    paths = [route.path for route in app.router.routes]
    assert paths.count("/internal/v17/team-event-certification-evidence/{candidate_id}") == 1
