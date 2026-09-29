from pathlib import Path


def test_universal_prop_lifecycle_is_not_recurring_on_interactive_runtime():
    repo_root = Path(__file__).resolve().parents[3]
    workflow_path = repo_root / ".github" / "workflows" / "wow-v17-prop-lifecycle-autopilot.yml"
    text = workflow_path.read_text(encoding="utf-8")

    # The universal all-route lifecycle remains available for explicit engineering
    # use and deploy-related pushes, but it must not recur against the 512 MiB
    # latency-sensitive web scorer. Recurring lifecycle work is handled by the
    # bounded, serial priority workflow instead.
    assert "schedule:" not in text
    assert "workflow_dispatch:" in text
    assert "push:" in text

    registration = text.split(
        "- name: Register exact basketball research candidates after runtime deploy",
        1,
    )[1].split("- name: Run bounded universal V17 prop lifecycle cycle", 1)[0]

    assert "github.event_name == 'push'" in registration
    assert "github.event_name == 'workflow_dispatch'" in registration
    assert "/internal/v17/nba-scalar-candidates/derive" in registration
    assert "/internal/v17/wnba-composite-candidate/derive" in registration

    wake = text.split("- name: Wake runtime before non-idempotent lifecycle work", 1)[1].split(
        "- name: Register exact basketball research candidates after runtime deploy",
        1,
    )[0]
    assert "/health/live" in wake
    assert "for attempt in $(seq 1 60)" in wake
    assert "--request POST" not in wake

    lifecycle = text.split("- name: Run bounded universal V17 prop lifecycle cycle", 1)[1]
    assert text.index("/health/live") < text.index("/v17/prop-lifecycle-autopilot-run")
    assert "/v17/prop-lifecycle-autopilot-run" in lifecycle
    assert 'WOW_CAN_EXECUTE: "false"' in text
    assert 'WOW_DRY_RUN_ONLY: "true"' in text

    priority_path = repo_root / ".github" / "workflows" / "wow-v17-priority-prop-lifecycle.yml"
    priority = priority_path.read_text(encoding="utf-8")
    assert 'cron: "7 * * * *"' in priority
    assert "max-parallel: 1" in priority
    assert "/v17/prop-lifecycle-autopilot-run" not in priority
    assert "/v17/prop-priority-settlement-run" in priority
    assert "/v17/prop-priority-durable-audit-run" in priority
