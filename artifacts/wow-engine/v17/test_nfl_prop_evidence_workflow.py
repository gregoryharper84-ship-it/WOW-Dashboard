from pathlib import Path


def test_nfl_prop_forward_evidence_workflow_preserves_fail_closed_authority():
    repo_root = Path(__file__).resolve().parents[3]
    text = (repo_root / ".github" / "workflows" / "wow-v17-nfl-prop-forward-evidence.yml").read_text()
    assert 'cron: "37 * * * *"' in text
    assert 'id-token: write' in text
    assert '/internal/v17/nfl-prop-forward-evidence/acquire' in text
    assert 'WOW_CAN_EXECUTE: "false"' in text
    assert 'WOW_DRY_RUN_ONLY: "true"' in text
    assert "automatic_certification" in text
    assert "automatic_promotion" in text
    assert "probability_publishable" in text
    assert "can_execute" in text



def test_nfl_hourly_producer_rotates_over_actual_candidate_page_count():
    repo_root = Path(__file__).resolve().parents[3]
    source = (repo_root / ".github" / "workflows" / "wow-v17-nfl-prop-forward-evidence.yml").read_text()
    assert 'candidate_rotation' in source
    assert 'GITHUB_RUN_NUMBER % 10' not in source
    assert '"max_candidates":32' in source
