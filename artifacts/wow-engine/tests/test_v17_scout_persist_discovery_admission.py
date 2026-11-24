"""Red-to-green regression for Scout persistence when upstream discovery was skipped.

Execute the actual GitHub Actions shell step using bounded fake GitHub API
responses; do not replace the guarded predicate with an imitation.
"""
import json
import os
from pathlib import Path
import subprocess
import textwrap

import pytest


WORKFLOW = Path(__file__).resolve().parents[3] / ".github/workflows/wow-v17-scout-brain-persist.yml"
STEP_NAME = "      - name: Locate recoverable Scout discovery artifact\n"
STUB = r"""
gh() {
  case "$2" in
    */artifacts) printf '%s\n' '' ;;
    */jobs?per_page=100) printf '%s' "$MOCK_JOBS" | jq -r "$4" ;;
    */actions/runs/*) printf '%s\n' "$MOCK_EVENT" ;;
    *) return 2 ;;
  esac
}
"""


def _actual_step() -> str:
    source = WORKFLOW.read_text(encoding="utf-8")
    assert source.count(STEP_NAME) == 1
    body = source.split(STEP_NAME, 1)[1].split("      - name: ", 1)[0]
    assert "run: |\n" in body
    return textwrap.dedent(body.split("run: |\n", 1)[1])


def _run(tmp_path, event: str, conclusions: list[str | None], *, include_job=True):
    jobs = [{"name": "Verify Multi-Scout contract", "status": "completed", "conclusion": "success"}]
    if include_job:
        jobs += [{"name": "Nightly all-sport discovery and governed specialist handoff",
                  "status": "completed", "conclusion": result} for result in conclusions]
    env = dict(os.environ, RUNNER_TEMP=str(tmp_path), GITHUB_OUTPUT=str(tmp_path / "output"),
               GITHUB_STEP_SUMMARY=str(tmp_path / "summary"), GITHUB_REPOSITORY="example/repo",
               SOURCE_RUN_ID="45435868712", MOCK_EVENT=event,
               MOCK_JOBS=json.dumps({"jobs": jobs}))
    return subprocess.run(["bash", "-e", "-o", "pipefail", "-c", STUB + _actual_step()],
                          env=env, text=True, capture_output=True, check=False)


def test_verified_skipped_workflow_run_has_no_artifact_and_is_not_a_persistence_failure(tmp_path):
    result = _run(tmp_path, "workflow_run", ["skipped"])
    assert result.returncode == 0
    assert "present=false" in (tmp_path / "output").read_text()
    assert "authenticated skipped discovery job" in (tmp_path / "summary").read_text()


@pytest.mark.parametrize("conclusions,include_job", [
    (["success"], True),
    (["failure"], True),
    (["cancelled"], True),
    (["skipped", "skipped"], True),
    ([], False),
])
def test_missing_discovery_artifact_fails_closed_unless_exactly_one_job_skipped(
    tmp_path, conclusions, include_job,
):
    result = _run(tmp_path, "workflow_run", conclusions, include_job=include_job)
    assert result.returncode != 0
    assert "persistence failed closed" in (tmp_path / "summary").read_text()


def test_push_verification_only_run_retains_legacy_no_artifact_behavior(tmp_path):
    result = _run(tmp_path, "push", ["skipped"])
    assert result.returncode == 0


def test_unrecognized_source_event_with_no_discovery_job_fails_closed(tmp_path):
    result = _run(tmp_path, "unknown", [], include_job=False)
    assert result.returncode != 0
