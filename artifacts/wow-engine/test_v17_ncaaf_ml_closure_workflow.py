from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "wow-v17-ncaaf-ml-closure.yml"


def _workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_ncaaf_ml_closure_verifies_immutable_candidate_before_freshness_gate() -> None:
    text = _workflow_text()

    maintenance = text.index('maintenance = post("/internal/v17/ncaaf-model-maintenance")')
    evidence = text.index('evidence = post(f"/internal/v17/team-event-certification-evidence/')
    replay = text.index('replay = post("/internal/v17/team-event-certification-replay")')
    freshness = text.index('"NCAAF_CURRENT_HISTORY_REFRESH_BLOCKED:"')

    assert maintenance < evidence < replay < freshness
    assert "NCAAF_FRESH_ACQUISITION_NOT_COMPLETE" not in text
    assert 'if evidence.get("status") != "CERTIFICATION_EVIDENCE_PASS":' in text
    assert 'lane.get("status") != "CERTIFICATION_REPLAY_PASS"' in text
    assert 'lane.get("candidate_bound_evidence_pass") is not True' in text


def test_ncaaf_ml_closure_preserves_non_promotion_governance() -> None:
    text = _workflow_text()

    assert 'WOW_CAN_EXECUTE: "false"' in text
    assert 'WOW_DRY_RUN_ONLY: "true"' in text
    assert 'assert maintenance.get("can_execute") is False' in text
    assert 'assert maintenance.get("automatic_certification") is False' in text
    assert 'assert maintenance.get("automatic_promotion") is False' in text
    assert 'assert maintenance.get("probability_publishable") is False' in text
    assert 'assert evidence.get("can_execute") is False' in text
    assert 'assert evidence.get("automatic_certification") is False' in text
    assert 'assert evidence.get("automatic_promotion") is False' in text
    assert 'assert evidence.get("probability_publishable") is False' in text
    assert 'assert replay.get("can_execute") is False' in text


def test_ncaaf_ml_closure_reruns_on_governed_workflow_change() -> None:
    text = _workflow_text()

    assert 'if: github.event_name == \'workflow_dispatch\' || github.event_name == \'push\'' in text
    assert '- ".github/workflows/wow-v17-ncaaf-ml-closure.yml"' in text
