from pathlib import Path


def test_scheduled_prop_lifecycle_skips_heavy_candidate_reregistration():
    repo_root = Path(__file__).resolve().parents[3]
    workflow_path = repo_root / ".github" / "workflows" / "wow-v17-prop-lifecycle-autopilot.yml"
    text = workflow_path.read_text(encoding="utf-8")

    assert 'cron: "*/15 * * * *"' in text

    registration = text.split(
        "- name: Register exact basketball research candidates after runtime deploy",
        1,
    )[1].split("- name: Run bounded universal V17 prop lifecycle cycle", 1)[0]

    assert "github.event_name == 'push'" in registration
    assert "github.event_name == 'workflow_dispatch'" in registration
    assert "github.event_name == 'schedule'" not in registration
    assert "/internal/v17/nba-scalar-candidates/derive" in registration
    assert "/internal/v17/wnba-composite-candidate/derive" in registration

    lifecycle = text.split("- name: Run bounded universal V17 prop lifecycle cycle", 1)[1]
    assert "/v17/prop-lifecycle-autopilot-run" in lifecycle
    assert 'WOW_CAN_EXECUTE: "false"' in text
    assert 'WOW_DRY_RUN_ONLY: "true"' in text
