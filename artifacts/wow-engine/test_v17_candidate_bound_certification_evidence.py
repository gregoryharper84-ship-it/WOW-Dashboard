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


def test_exact_candidate_receipt_clears_only_evidence_blockers():
    row = _candidate()
    report = build_certification_report([row], certification_evidence_receipts=[_receipt(row)])
    lane = next(item for item in report["sports"] if item["sport"] == "NCAAF")["lanes"][0]
    assert lane["status"] == "CERTIFICATION_REPLAY_PASS"
    assert lane["candidate_bound_evidence_pass"] is True
    assert lane["probability_publishable"] is False
    assert lane["can_execute"] is False


def test_stale_or_failed_receipt_does_not_count():
    row = _candidate()
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
