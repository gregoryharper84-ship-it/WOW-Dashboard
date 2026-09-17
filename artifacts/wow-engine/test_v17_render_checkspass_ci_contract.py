from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = {
    "wow-verify": ROOT / ".github" / "workflows" / "wow-verify.yml",
    "wow-engine-verify": ROOT / ".github" / "workflows" / "wow-engine-verify.yml",
}


def test_render_checkspass_verifiers_run_on_main_and_never_cancel_main_runs():
    for workflow_name, path in WORKFLOWS.items():
        text = path.read_text()
        assert "pull_request:\n    branches: [main]" in text, workflow_name
        assert "push:\n    branches: [main]" in text, workflow_name
        assert (
            f"group: {workflow_name}-${{{{ github.event_name }}}}-${{{{ github.ref }}}}"
            in text
        ), workflow_name
        assert (
            "cancel-in-progress: ${{ github.event_name == 'pull_request' }}" in text
        ), workflow_name
        assert "cancel-in-progress: true" not in text, workflow_name


def test_render_service_remains_ci_gated_and_non_executable():
    render = (ROOT / "render.yaml").read_text()
    assert "name: wow-governed-probability-engine" in render
    assert "autoDeployTrigger: checksPass" in render
    assert 'key: WOW_CAN_EXECUTE\n        value: "false"' in render
    assert 'key: WOW_DRY_RUN_ONLY\n        value: "true"' in render
