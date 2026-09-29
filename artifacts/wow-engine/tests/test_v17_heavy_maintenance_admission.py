from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]

HEAVY_GROUP = "wow-v17-production-heavy-maintenance"
WORKFLOWS = (
    ROOT / ".github/workflows/wow-v17-first-six-model-maintenance.yml",
    ROOT / ".github/workflows/wow-v17-priority-prop-lifecycle.yml",
    ROOT / ".github/workflows/wow-v17-spread-certification-replay.yml",
    ROOT / ".github/workflows/wow-v17-spread-forward-production-canary.yml",
)


def test_heavy_production_workflows_share_admission_group():
    for path in WORKFLOWS:
        text = path.read_text()
        assert HEAVY_GROUP in text, path
        assert "cancel-in-progress: false" in text, path


def test_priority_lifecycle_remains_serial_and_nonexecuting():
    text = WORKFLOWS[1].read_text()
    assert "max-parallel: 1" in text
    assert 'WOW_CAN_EXECUTE: "false"' in text
    assert 'WOW_DRY_RUN_ONLY: "true"' in text


def test_spread_canaries_remain_nonexecuting():
    text = WORKFLOWS[3].read_text()
    assert 'WOW_CAN_EXECUTE: "false"' in text
    assert 'WOW_DRY_RUN_ONLY: "true"' in text
