"""Guard lossless cancelled-writer recovery against source runs without discovery bytes.

These tests execute the exact Bash selector embedded in the workflow using a
local gh stub. No GitHub mutation, provider credentials, DB or network needed.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/wow-v17-scout-persist-resume.yml"


def _selector() -> str:
    workflow = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    script = workflow["jobs"]["resume-persistence"]["steps"][0]["run"]
    match = re.search(
        r"(?ms)^resolve_oldest_cancelled_without_receipt\(\) \{\n.*?^\}",
        script,
    )
    assert match, "The source selector must remain independently testable."
    return "set -euo pipefail\n" + match.group(0) + "\nresolve_oldest_cancelled_without_receipt\n"


def _execute(tmp_path: Path, *, valid_source: str = "", broken_source: str = "",
             attempted_missing_source: str = "", broken_jobs_source: str = ""):
    gh = tmp_path / "gh"
    gh.write_text(
        """#!/usr/bin/env python3
import json
import os
import re
import sys

assert sys.argv[1] == "api"
url = sys.argv[2]
if "wow-v17-scout-brain-persist.yml/runs?" in url:
    if "page=1" not in url:
        raise SystemExit(33)
    payload = {"workflow_runs": [
        {"created_at": "2026-10-23T14:58:23Z", "display_title": "writer source=39974206880"},
        {"created_at": "2026-10-24T14:58:23Z", "display_title": "writer source=39974206881"}
    ]}
elif "/actions/artifacts?name=wow-v17-scout-brain-persistence-" in url:
    payload = {"artifacts": []}
elif "/actions/runs/" in url and "/jobs" in url:
    source_id = re.search(r"/actions/runs/([0-9]+)/jobs", url).group(1)
    if source_id == os.environ.get("MOCK_BROKEN_JOBS_SOURCE"):
        raise SystemExit(46)
    conclusion = (
        "success" if source_id == os.environ.get("MOCK_ATTEMPTED_MISSING_SOURCE")
        else "skipped"
    )
    payload = {"total_count": 1, "jobs": [{
        "name": "Nightly all-sport discovery and governed specialist handoff",
        "status": "completed", "conclusion": conclusion,
    }]}
elif "/actions/runs/" in url and "/artifacts" in url:
    source_id = re.search(r"/actions/runs/([0-9]+)/artifacts", url).group(1)
    if source_id == os.environ.get("MOCK_BROKEN_SOURCE"):
        raise SystemExit(44)
    artifacts = [
        # An expired matching discovery must never be selected.
        {"name": "wow-v17-multiscout-wow-scout-expired-discovery", "expired": True},
        # An unrelated non-expired artifact must never be selected.
        {"name": "unrelated-run-artifact", "expired": False},
    ]
    if source_id == os.environ.get("MOCK_VALID_SOURCE"):
        artifacts.append({
            "name": "wow-v17-multiscout-wow-scout-events-discovery",
            "expired": False,
        })
    payload = {"artifacts": artifacts}
else:
    raise SystemExit(45)

if "--jq" in sys.argv:
    import subprocess
    expression = sys.argv[sys.argv.index("--jq") + 1]
    result = subprocess.run(
        ["jq", "-r", expression], input=json.dumps(payload),
        capture_output=True, text=True, check=False,
    )
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    raise SystemExit(result.returncode)
print(json.dumps(payload))
""",
        encoding="utf-8",
    )
    gh.chmod(0o755)
    summary = tmp_path / "summary.txt"
    summary.write_text("", encoding="utf-8")
    env = os.environ.copy()
    env.update(
        {
            "PATH": str(tmp_path) + os.pathsep + os.environ.get("PATH", ""),
            "GITHUB_REPOSITORY": "gregoryharper84-ship-it/WOW-Dashboard",
            "GITHUB_STEP_SUMMARY": str(summary),
            "SCOUT_PERSIST_LOSSLESS_RECOVERY_EPOCH": "2026-10-03T04:44:53Z",
            "SCOUT_PERSIST_RECOVERY_MAX_PAGES": "20",
            "MOCK_VALID_SOURCE": valid_source,
            "MOCK_BROKEN_SOURCE": broken_source,
            "MOCK_ATTEMPTED_MISSING_SOURCE": attempted_missing_source,
            "MOCK_BROKEN_JOBS_SOURCE": broken_jobs_source,
        }
    )
    result = subprocess.run(
        ["bash", "-c", _selector()],
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=15,
    )
    return result, summary.read_text(encoding="utf-8")


def test_oldest_invalid_source_is_logged_then_next_valid_source_selected(tmp_path):
    result, summary = _execute(tmp_path, valid_source="39974206881")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "39974206881"
    assert (
        "SCOUT_DISCOVERY_ARTIFACT_UNOBTAINABLE source_run_id=39974206880"
        in summary
    )


def test_no_recoverable_payload_retains_hold_and_accounts_for_each_source(tmp_path):
    result, summary = _execute(tmp_path)
    assert result.returncode == 1, result.stderr
    assert result.stdout.strip() == ""
    for source in ("39974206880", "39974206881"):
        assert f"SCOUT_DISCOVERY_ARTIFACT_UNOBTAINABLE source_run_id={source}" in summary


def test_discovery_api_failure_fails_closed_not_skipped(tmp_path):
    result, summary = _execute(tmp_path, broken_source="39974206880")
    assert result.returncode == 2
    assert result.stdout.strip() == ""
    assert "39974206881" not in summary


def test_attempted_discovery_with_missing_artifact_holds_instead_of_skipping(tmp_path):
    # No evidence is discarded just because a later source has an artifact.
    result, summary = _execute(
        tmp_path, valid_source="39974206881",
        attempted_missing_source="39974206880",
    )
    assert result.returncode == 2, result.stderr
    assert result.stdout.strip() == ""
    assert "SCOUT_DISCOVERY_ARTIFACT_UNOBTAINABLE source_run_id=39974206880" in summary
    assert "recovery_class=EVIDENCE_PENDING" in summary
    assert "39974206881" not in result.stdout


def test_unverifiable_discovery_job_inventory_fails_closed(tmp_path):
    result, summary = _execute(tmp_path, broken_jobs_source="39974206880")
    assert result.returncode == 2, result.stderr
    assert result.stdout.strip() == ""
    assert "39974206881" not in summary


def test_skipped_discovery_sources_are_not_mistaken_for_data_loss(tmp_path):
    result, summary = _execute(tmp_path, valid_source="39974206881")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "39974206881"
    assert (
        "provenance=verified_discovery_job_skipped recovery_class=NOT_APPLICABLE"
        in summary
    )
