from pathlib import Path

from v17.team_event_certification_replay import assess_candidate


def _candidate(status: str):
    return {
        "candidate_id": "11111111-1111-1111-1111-111111111111",
        "sport": "SOCCER",
        "league": "EPL",
        "model_family": "SOCCER_EPL_MODEL",
        "model_artifact_version": "soccer-epl-v1",
        "training_dataset_hash": "a" * 64,
        "training_code_sha": "b" * 40,
        "artifact_checksum": "c" * 64,
        "training_rows": 600,
        "calibration_rows": 200,
        "test_rows": 200,
        "research_screen_pass": True,
        "source_review_status": status,
        "lifecycle_state": "CANDIDATE",
        "promoted": False,
        "active": False,
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "can_execute": False,
    }


def test_provenance_ready_states_do_not_clear_source_review():
    for status in ("CC_BY_4_0_PROVENANCE_READY", "CC0_PUBLIC_DOMAIN_PROVENANCE_READY"):
        result = assess_candidate("SOCCER", _candidate(status)).as_dict()
        assert result["status"] == "SOURCE_REVIEW_PENDING"
        assert "SOURCE_REVIEW_REQUIRED" in result["blockers"]
        assert result["probability_publishable"] is False
        assert result["can_execute"] is False


def test_only_pass_can_clear_source_review_gate_when_replay_evidence_exists():
    result = assess_candidate("SOCCER", _candidate("PASS"), replay_evidence_pass=True).as_dict()
    assert result["status"] == "CERTIFICATION_REPLAY_PASS"
    assert result["probability_publishable"] is False
    assert result["automatic_promotion"] is False


def test_migration_versions_pre_review_states():
    repo_root = Path(__file__).resolve().parents[2]
    sql = (repo_root / "artifacts/wow-engine/migrations/20260919_v17_d1_source_review_provenance_pending_states.sql").read_text()
    assert "CC_BY_4_0_PROVENANCE_READY" in sql
    assert "CC0_PUBLIC_DOMAIN_PROVENANCE_READY" in sql
    assert "PASS alone clears source review" in sql
