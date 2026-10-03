from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
STRESS = ROOT / ".github/workflows/wow-v17-scout-persistence-stress.yml"
CAPTAIN = ROOT / ".github/workflows/wow-v17-morning-green-continuation.yml"


def test_scout_stress_workflow_is_manual_staging_safe_and_10x_by_default():
    text = STRESS.read_text()
    data = yaml.safe_load(text)
    assert data["name"] == "wow-v17-scout-persistence-stress"
    assert "workflow_dispatch:" in text
    assert 'default: "10"' in text
    assert 'default: "30"' in text
    assert "WOW_SCOUT_STRESS_ALLOWED_HOST" in text
    assert "WOW_SCOUT_STRESS_TOKEN" in text
    assert "production_target_forbidden: true" in text
    assert "production_canary_required: true" in text
    assert "can_execute: false" in text
    assert data["permissions"]["contents"] == "read"


def test_existing_morning_green_workflow_is_merge_captain():
    text = CAPTAIN.read_text()
    assert "Re-verify all protected checks on exact head SHA" in text
    assert "Verify trusted exact-head governance receipt" in text
    assert "Verify Morning-Green diff scope" in text
    assert 'gh pr merge "$PR_NUMBER"' in text
    assert "--match-head-commit" in text
    assert "Morning-Green autonomous merge denied" in text
    assert "can_execute: false" in text
