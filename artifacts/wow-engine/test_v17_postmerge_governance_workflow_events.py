"""Regression for the real release-verifier shell provenance gate on protected main.

Read and execute the current YAML shell case block; reject unsupported events and
untrusted branch/workflow identities. This is not an authorization grant.
"""
from __future__ import annotations

import shlex
import subprocess
import textwrap
from pathlib import Path

import pytest


WORKFLOW = (
    Path(__file__).resolve().parents[2]
    / ".github/workflows/wow-v17-release-production-verification-agent.yml"
)
GOVERNANCE_NAME = "wow-v17-existing-pr-governance-review"
GOVERNANCE_PATH = ".github/workflows/wow-v17-existing-pr-governance-review.yml"


def _actual_shell_gate() -> str:
    source = WORKFLOW.read_text(encoding="utf-8")
    start = source.index("          protected_main_source=false\n", source.index("- name: Verify governance-producing workflow provenance"))
    stop = source.index('          [ "$status" = "completed" ]', start)
    # This exercises the exact production shell case code rather than a
    # reimplemented Python acceptance table.
    return textwrap.dedent(source[start:stop])


def _admitted(*, event: str, name: str = GOVERNANCE_NAME,
              path: str = GOVERNANCE_PATH, branch: str = "main") -> bool:
    variables = {"event": event, "name": name, "path": path, "head_branch": branch}
    assignments = "\n".join(f"{k}={shlex.quote(v)}" for k, v in variables.items())
    shell = (
        "set -euo pipefail\n" + assignments + "\n"
        + _actual_shell_gate() + '\n[ "$protected_main_source" = "true" ]\n'
    )
    return subprocess.run(["bash", "-c", shell], capture_output=True,
                          text=True, check=False).returncode == 0


@pytest.mark.parametrize("event", [
    "pull_request_target", "workflow_dispatch", "workflow_run", "check_run",
])
def test_known_existing_pr_producer_events_on_protected_main(event: str) -> None:
    assert _admitted(event=event)


@pytest.mark.parametrize("event", ["push", "pull_request", "schedule", "check_suite", "repository_dispatch"])
def test_unknown_existing_pr_producer_events_rejected(event: str) -> None:
    assert not _admitted(event=event)


@pytest.mark.parametrize("branch", ["feature/candidate", "refs/pull/2154/merge", "", "staging"])
def test_check_run_must_be_protected_main(branch: str) -> None:
    assert not _admitted(event="check_run", branch=branch)


def test_check_run_cannot_impersonate_other_workflow_identity() -> None:
    assert not _admitted(event="check_run", name="wow-v17-claude-engineering-worker",
                         path=".github/workflows/wow-v17-claude-engineering-worker.yml")
    assert not _admitted(event="check_run", name=GOVERNANCE_NAME,
                         path=".github/workflows/other.yml")
    assert not _admitted(event="check_run", name="forged-producer",
                         path=GOVERNANCE_PATH)


def test_downstream_status_and_repository_identity_guards_retained() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    section = source.split("- name: Verify governance-producing workflow provenance", 1)[1]
    assert '[ "$status" = "completed" ] && [ "$conclusion" = "success" ]' in section
    assert '[ "$head_repo" = "$GITHUB_REPOSITORY" ]' in section
    assert "RELEASE_GOVERNANCE_WORKFLOW_REPOSITORY_MISMATCH" in section
    assert "RELEASE_GOVERNANCE_WORKFLOW_NOT_SUCCESSFUL" in section
