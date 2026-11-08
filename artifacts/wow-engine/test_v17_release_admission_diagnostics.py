"""The automatic Release admission callback must name its typed refusal.

A `workflow_run` callback executes against protected `main`, so an opaque shell
failure here publishes a failing check on whichever commit Render's `checksPass`
trigger is currently evaluating. These tests pin two properties:

* a refused policy resolution still fails closed and publishes no Release check;
* the exact typed reason is visible on the check annotation and job summary, so
  the next occurrence is diagnosable without server-side log access.
"""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/wow-v17-release-authority-check.yml"


def _load():
    return yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def test_admission_surfaces_the_typed_policy_refusal():
    admission = _load()["jobs"]["admit-release"]
    step = next(s for s in admission["steps"] if s.get("id") == "target")
    run = step["run"]
    assert 'if ! bundle=$(python "$tool" "${args[@]}" 2>"$err"); then' in run
    assert "resolve-qa-policy.err" in run
    assert 'echo "::error::${reason}"' in run
    assert "GITHUB_STEP_SUMMARY" in run


def test_a_refused_policy_still_exits_nonzero_and_publishes_nothing():
    run = next(
        s for s in _load()["jobs"]["admit-release"]["steps"] if s.get("id") == "target"
    )["run"]
    # The typed-reason block must terminate before the neutral short-circuit.
    reason_block = run.split("if ! bundle=", 1)[1]
    assert reason_block.split("fi", 1)[0].count("exit 1") == 1
    # Neutrality stays reserved for a resolved superseded policy, never a refusal.
    assert '| "$(jq -r .disposition // empty' in run or '"$(jq -r \'.disposition // empty\' <<< "$bundle")" = "neutral"' in run


def test_release_lane_cannot_run_without_a_successful_admission():
    workflow = _load()
    release = workflow["jobs"]["release"]
    assert release["needs"] == "admit-release"
    assert release["if"].startswith("needs.admit-release.result == 'success'")
    assert "needs.admit-release.outputs.applicable == 'true'" in release["if"]
