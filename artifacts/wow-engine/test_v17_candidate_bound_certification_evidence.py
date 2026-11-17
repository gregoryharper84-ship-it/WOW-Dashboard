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


def _receipt(row, **overrides):
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


def test_exact_candidate_receipt_does_not_self_approve_required_source_review():
    row = _candidate()
    report = build_certification_report([row], certification_evidence_receipts=[_receipt(row)])
    lane = next(item for item in report["sports"] if item["sport"] == "NCAAF")["lanes"][0]
    assert lane["status"] == "SOURCE_REVIEW_PENDING"
    assert "SOURCE_REVIEW_REQUIRED" in lane["blockers"]
    assert lane["candidate_bound_evidence_pass"] is False
    assert lane["probability_publishable"] is False
    assert lane["can_execute"] is False


def test_exact_candidate_receipt_clears_replay_only_after_governed_source_review():
    row = _candidate(source_review_status="PASS")
    report = build_certification_report([row], certification_evidence_receipts=[_receipt(row)])
    lane = next(item for item in report["sports"] if item["sport"] == "NCAAF")["lanes"][0]
    assert lane["status"] == "CERTIFICATION_REPLAY_PASS"
    assert lane["candidate_bound_evidence_pass"] is True
    assert lane["probability_publishable"] is False
    assert lane["can_execute"] is False


def test_stale_or_failed_receipt_does_not_count():
    # With source rights already reviewed, exercise only receipt identity/replay.
    row = _candidate(source_review_status="PASS")
    stale = _receipt(row, candidate_id="22222222-2222-2222-2222-222222222222")
    failed = _receipt(row, replay_status="FAIL")
    for proof in (stale, failed):
        report = build_certification_report([row], certification_evidence_receipts=[proof])
        lane = next(item for item in report["sports"] if item["sport"] == "NCAAF")["lanes"][0]
        assert lane["candidate_bound_evidence_pass"] is False
        assert lane["probability_publishable"] is False


def test_candidate_evidence_route_install_is_idempotent():
    app = FastAPI()
    evidence.install_candidate_certification_evidence_route(app, auth_dependency=lambda: None, db_client_fn=lambda: None)
    evidence.install_candidate_certification_evidence_route(app, auth_dependency=lambda: None, db_client_fn=lambda: None)
    assert [route.path for route in app.router.routes].count(
        "/internal/v17/team-event-certification-evidence/{candidate_id}"
    ) == 1


def test_candidate_verifier_separates_frozen_replay_from_source_rights(monkeypatch):
    from dataclasses import dataclass
    from types import SimpleNamespace
    import v17.binary_candidate_lifecycle as lifecycle

    @dataclass
    class Metrics:
        brier: float = 0.25

    artifact = {"feature_names": ["p"]}
    calibrator = {"method": "frozen"}
    family = "NCAAF_DYNAMIC_TEAM_STATE_LOGIT_V2"
    dataset_hash = "a" * 64
    code_sha = "b" * 40
    manifest = {"source": "SPORTSDATAVERSE_NCAA_MFB_PLAYER_STATS"}
    row = {
        "official_event_id": "fixture-event-1",
        "event_start_time": "2026-09-20T20:00:00+00:00",
        "feature_as_of": "2026-09-19T20:00:00+00:00",
        "features": {"p": 0.4},
        "outcome_json": {"home_win": True},
        "source_manifest": manifest,
        "source_manifest_sha256": evidence._hash(manifest),
        "market_features_used": False,
        "can_execute": False,
    }
    replay = SimpleNamespace(
        dataset_hash=dataset_hash, artifact_payload=artifact,
        calibrator_payload=calibrator, metrics=Metrics(),
    )

    class Query:
        def __init__(self, candidate):
            self.candidate = candidate

        def select(self, *_args):
            return self

        def eq(self, *_args):
            return self

        def limit(self, *_args):
            return self

        def execute(self):
            return SimpleNamespace(data=[self.candidate])

    class DB:
        def __init__(self, candidate):
            self.candidate = candidate

        def table(self, name):
            assert name == "wow_d1_candidate_artifacts"
            return Query(self.candidate)

    monkeypatch.setattr(evidence, "_all_rows", lambda _db, _candidate: [row])
    replay_value = [replay]
    monkeypatch.setattr(
        lifecycle, "train_binary_candidate",
        lambda *_args, **_kwargs: replay_value[0],
    )
    captured = []

    def persist(_db, _candidate, source_pass, replay_pass, payload):
        captured.append((source_pass, replay_pass, payload))
        return {
            "source_review_status": "PASS" if source_pass else "FAIL",
            "replay_status": "PASS" if replay_pass else "FAIL",
            "receipt_id": "fixture", "evidence_sha256": "c" * 64,
        }

    monkeypatch.setattr(evidence, "_persist", persist)

    for review, expected_source in (
        ("REQUIRED", False),
        ("CC_BY_4_0_PROVENANCE_READY", False),
        ("FAIL", False),
        ("PASS", True),
    ):
        candidate = _candidate(
            source_review_status=review,
            source_policy_id="TEAM_STATE_DYNAMIC_PRIOR_ONLY_V1",
            feature_schema_version="NCAAF_DYNAMIC_TEAM_STATE_FEATURES_V2",
            training_rows=1, calibration_rows=0, test_rows=0,
            model_artifact_version=f"{family}_{dataset_hash[:16]}_{code_sha[:12]}",
            artifact_payload=artifact, calibrator_payload=calibrator,
            artifact_checksum=evidence._hash(artifact),
            validation_metrics={"brier": 0.25},
        )
        response = evidence.verify_candidate_certification_evidence(
            DB(candidate), candidate["candidate_id"]
        )
        assert captured[-1][0] is expected_source, review
        assert captured[-1][1] is True, review  # Mathematical replay stays diagnostic
        assert response["status"] == (
            "CERTIFICATION_EVIDENCE_PASS" if expected_source else "CERTIFICATION_EVIDENCE_FAILED"
        )
        assert response["probability_publishable"] is False
        assert response["can_execute"] is False
        if not expected_source:
            assert "SOURCE_REVIEW_" in " ".join(response["blockers"])

    # A timezone-naive feature timestamp cannot prove chronological pregame
    # capture. The verifier returns a typed hold even if source review is PASS.
    row["feature_as_of"] = "2026-09-19T20:00:00"
    response = evidence.verify_candidate_certification_evidence(
        DB(candidate), candidate["candidate_id"]
    )
    assert response["status"] == "CERTIFICATION_EVIDENCE_FAILED"
    assert "CANDIDATE_TIMESTAMP_INVALID_OR_NAIVE" in response["blockers"]
    assert captured[-1][1] is False
    assert response["probability_publishable"] is False
    row["feature_as_of"] = "2026-09-19T20:00:00+00:00"

    # A missing replay object can never manufacture a PASS even if reviewed.
    replay_value[0] = None
    response = evidence.verify_candidate_certification_evidence(
        DB(candidate), candidate["candidate_id"]
    )
    assert response["status"] == "CERTIFICATION_EVIDENCE_FAILED"
    assert captured[-1][1] is False


def test_candidate_chronology_requires_explicit_timezone():
    import pytest

    for invalid in ("2026-09-20T20:00:00", "not-a-timestamp"):
        with pytest.raises(ValueError, match="CANDIDATE_TIMESTAMP_INVALID_OR_NAIVE"):
            evidence._iso(invalid)
    assert evidence._iso("2026-09-20T20:00:00-05:00") == "2026-09-21T01:00:00+00:00"
