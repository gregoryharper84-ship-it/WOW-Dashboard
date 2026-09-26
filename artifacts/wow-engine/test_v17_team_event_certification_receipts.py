from v17.team_event_certification_replay import build_certification_report


def _candidate(**overrides):
    row = {
        "candidate_id": "11111111-1111-1111-1111-111111111111",
        "created_at": "2026-09-25T18:01:15+00:00",
        "sport": "WNBA",
        "league": "WNBA",
        "model_family": "WNBA_DYNAMIC_TEAM_STATE_LOGIT_V2",
        "model_artifact_version": "WNBA_DYNAMIC_TEAM_STATE_LOGIT_V2_deadbeef",
        "training_dataset_hash": "a" * 64,
        "training_code_sha": "b" * 40,
        "artifact_checksum": "c" * 64,
        "training_rows": 1273,
        "calibration_rows": 424,
        "test_rows": 425,
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
        "created_at": "2026-09-25T20:45:00+00:00",
        "evidence_version": "WNBA_TEAM_EVENT_CERTIFICATION_EVIDENCE_V1",
        "lane_id": "WNBA:WNBA:WNBA_DYNAMIC_TEAM_STATE_LOGIT_V2",
        "model_artifact_version": candidate["model_artifact_version"],
        "training_dataset_hash": candidate["training_dataset_hash"],
        "artifact_checksum": candidate["artifact_checksum"],
        "source_review_pass": True,
        "replay_evidence_pass": True,
        "probability_publishable": False,
        "can_execute": False,
    }
    row.update(overrides)
    return row


def _wnba_lane(report):
    wnba = next(row for row in report["sports"] if row["sport"] == "WNBA")
    assert len(wnba["lanes"]) == 1
    return wnba, wnba["lanes"][0]


def test_exact_receipt_can_clear_source_and_replay_without_promotion():
    candidate = _candidate()
    receipt = _receipt(candidate)
    report = build_certification_report(
        [candidate],
        certification_evidence_by_candidate={candidate["candidate_id"]: receipt},
    )
    wnba, lane = _wnba_lane(report)
    assert wnba["status"] == "CERTIFICATION_REPLAY_PASS"
    assert lane["status"] == "CERTIFICATION_REPLAY_PASS"
    assert lane["blockers"] == []
    assert lane["source_review_status"] == "PASS_BY_BOUND_EVIDENCE_RECEIPT"
    assert lane["certification_evidence_receipt_bound"] is True
    assert lane["probability_publishable"] is False
    assert lane["can_execute"] is False
    assert report["automatic_certification"] is False
    assert report["automatic_promotion"] is False


def test_stale_or_mismatched_receipt_fails_closed():
    candidate = _candidate()
    receipt = _receipt(candidate, training_dataset_hash="f" * 64)
    report = build_certification_report(
        [candidate],
        certification_evidence_by_candidate={candidate["candidate_id"]: receipt},
    )
    wnba, lane = _wnba_lane(report)
    assert wnba["status"] == "SOURCE_REVIEW_PENDING"
    assert lane["certification_evidence_receipt_bound"] is False
    assert "SOURCE_REVIEW_REQUIRED" in lane["blockers"]
    assert "PROSPECTIVE_OR_CERTIFICATION_REPLAY_EVIDENCE_REQUIRED" in lane["blockers"]


def test_explicit_source_review_failure_cannot_be_overridden_by_pass_receipt():
    candidate = _candidate(source_review_status="FAIL")
    receipt = _receipt(candidate)
    report = build_certification_report(
        [candidate],
        certification_evidence_by_candidate={candidate["candidate_id"]: receipt},
    )
    wnba, lane = _wnba_lane(report)
    assert wnba["status"] == "SOURCE_REVIEW_FAILED"
    assert lane["status"] == "SOURCE_REVIEW_FAILED"
    assert "SOURCE_REVIEW_FAILED" in lane["blockers"]
    assert lane["probability_publishable"] is False
    assert lane["can_execute"] is False
