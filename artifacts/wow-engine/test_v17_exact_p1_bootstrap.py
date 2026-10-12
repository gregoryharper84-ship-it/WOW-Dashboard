"""Trust-root bootstrap regression checks. This does NOT self-certify trusted workflows.

Independent bootstrap authority, SIRT and protected exact-head review are
required before these workflow changes can be merged.
"""
from pathlib import Path

import pytest
import yaml


WORKFLOW_DIR = Path(__file__).resolve().parents[2] / ".github" / "workflows"
PRIMARY = "wow-v17-chatgpt-engineering-worker.yml"
FALLBACK = "wow-v17-claude-engineering-worker.yml"
PROVIDER = "wow-v17-engineering-provider-dispatcher.yml"


@pytest.mark.parametrize("workflow", [PRIMARY, FALLBACK])
def test_exact_p1_is_bound_to_approved_standard_global_lease(workflow):
    text = (WORKFLOW_DIR / workflow).read_text()
    assert 'record=$(jq -c --arg id "$TARGET_INCIDENT"' in text
    assert 'TARGET_INCIDENT_NOT_ACTIONABLE' in text
    assert '[ "$severity" = "P1" ] && [ "$lane" = "STANDARD" ]' in text
    assert 'STANDARD_TARGET_MUST_USE_GLOBAL_LEASE' in text
    assert 'lease_group=GLOBAL' in text
    assert 'EXACT_P1_GLOBAL_LEASE' in text
    assert 'TARGET_INCIDENT_LEASE_MISMATCH' in text
    assert 'TARGET_INCIDENT_NOT_SUPPORTED' in text
    assert 'target_hold' in text
    assert 'target_reason' in text
    assert 'V17_TERMINAL_REDUCER' in text
    assert 'can_execute:false' in text


@pytest.mark.parametrize("workflow", [PRIMARY, FALLBACK])
def test_exact_p0_is_still_domain_fenced(workflow):
    text = (WORKFLOW_DIR / workflow).read_text()
    assert '[ "$severity" = "P0" ] && [ "$lane" = "RAPID" ]' in text
    assert 'P0_DOMAIN_LEASE_MISSING' in text
    assert 'DOMAIN_SCOPED_P0_LEASE' in text
    assert 'TARGET_INCIDENT_LEASE_MISMATCH' in text
    assert 'TARGET_INCIDENT_NOT_ACTIONABLE' in text


def test_provider_preserves_numeric_target_and_defers_lease_to_worker():
    text = (WORKFLOW_DIR / PROVIDER).read_text()
    assert 'TARGET_INCIDENT_INVALID' in text
    assert 'TARGET_INCIDENT_REQUIRES_DOMAIN_LEASE' not in text
    assert '[[ "$target_incident" =~ ^[0-9]+$ ]]' in text
    assert "wow-v17-chatgpt-engineering-worker.yml" in text
    assert "wow-v17-claude-engineering-worker.yml" in text


def test_workflow_syntax_and_independent_trust_root_protection():
    for name in (PRIMARY, FALLBACK, PROVIDER):
        yaml.safe_load((WORKFLOW_DIR / name).read_text())
    gate = (WORKFLOW_DIR / "wow-v17-existing-pr-governance-review.yml").read_text()
    for name in (PRIMARY, FALLBACK, PROVIDER):
        assert name in gate
    assert "EXISTING_PR_TRUST_ROOT_CHANGE_REQUIRES_WORKER_OR_BOOTSTRAP" in gate


def test_bootstrap_does_not_authorize_execution_or_merge():
    for name in (PRIMARY, FALLBACK, PROVIDER):
        text = (WORKFLOW_DIR / name).read_text()
        assert "can_execute:true" not in text
        assert "terminal_authority=V17_TERMINAL_REDUCER" not in text or "V17_TERMINAL_REDUCER" in text
    gate = (WORKFLOW_DIR / "wow-v17-trusted-governance-gate.yml").read_text()
    assert "GOVERNANCE_WORKFLOW_IDENTITY_MISMATCH" in gate


@pytest.mark.parametrize("workflow", [PRIMARY, FALLBACK])
@pytest.mark.parametrize(
    "incident,requested_lease,records,expected_reason,expected_target",
    [
        ("1388", "P0_LLP_RESTORE",
         [{"incident_id": "1388", "severity": "P0", "execution_lane": "RAPID",
           "lease_group": "P0_LLP_RESTORE"}], None, "1388"),
        ("823", "GLOBAL",
         [{"incident_id": "1388", "severity": "P0", "execution_lane": "RAPID",
           "lease_group": "P0_LLP_RESTORE"},
          {"incident_id": "823", "severity": "P1", "execution_lane": "STANDARD",
           "lease_group": "GLOBAL"}], None, "823"),
        ("1388", "GLOBAL",
         [{"incident_id": "1388", "severity": "P0", "execution_lane": "RAPID",
           "lease_group": "P0_LLP_RESTORE"}], "TARGET_INCIDENT_LEASE_MISMATCH", None),
        ("823", "P1_UNTRUSTED",
         [{"incident_id": "823", "severity": "P1", "execution_lane": "STANDARD",
           "lease_group": "GLOBAL"}], "TARGET_INCIDENT_LEASE_MISMATCH", None),
        ("823", "GLOBAL",
         [{"incident_id": "823", "severity": "P1", "execution_lane": "STANDARD",
           "lease_group": "P1_UNTRUSTED"}], "STANDARD_TARGET_MUST_USE_GLOBAL_LEASE", None),
        ("823", "GLOBAL",
         [{"incident_id": "823", "severity": "P2", "execution_lane": "STANDARD",
           "lease_group": "GLOBAL"}], "TARGET_INCIDENT_NOT_SUPPORTED", None),
        ("823", "GLOBAL", [], "TARGET_INCIDENT_NOT_ACTIONABLE", None),
    ],
)
def test_worker_route_shell_execution_is_fail_closed(
    workflow, incident, requested_lease, records, expected_reason, expected_target,
    tmp_path,
):
    """Execute exact protected-worker Bash/JQ target-selection, not rewritten rules."""
    import json
    import os
    import re
    import shutil
    import subprocess

    if shutil.which("bash") is None or shutil.which("jq") is None:
        pytest.skip("Bash and JQ are required by protected GitHub runner")
    doc = yaml.safe_load((WORKFLOW_DIR / workflow).read_text())
    job = doc["jobs"]["multi-agent-engineering"]
    step = next(s for s in job["steps"] if s.get("name") == "Build live dual-stream dispatch plan")
    source = step["run"]
    begin = source.index('if [ -n "${TARGET_INCIDENT:-}" ]; then')
    rest = source[begin:]
    branch = re.search(r"\n\s*else\n\s+jq '\.records \|=", rest)
    assert branch is not None, "Exact target branch must remain structurally identifiable"
    actual_shell = rest[:branch.start()] + '\nfi\nprintf "%s\\n" "$decision"\n'
    queue = tmp_path / "wow-dispatch-queue.json"
    queue.write_text(json.dumps({"records": records}))
    output = tmp_path / "github-output"
    output.write_text("")
    environment = {
        **os.environ,
        "TARGET_INCIDENT": incident,
        "REQUESTED_LEASE_GROUP": requested_lease,
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_OUTPUT": str(output),
    }
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", actual_shell],
        text=True, capture_output=True, env=environment, check=False,
        timeout=10,
    )
    if expected_reason:
        assert result.returncode != 0
        assert expected_reason in result.stderr
    else:
        assert result.returncode == 0, result.stderr
        decision = json.loads(result.stdout)
        assert decision["restoration"]["incident_id"] == expected_target
        assert decision["acceleration"]["incident_id"] is None
        assert decision["can_execute"] is False
        assert decision["terminal_authority"] == "V17_TERMINAL_REDUCER"


def test_bootstrap_typed_codes_are_registered():
    """Process Addendum section 4: every code this change emits is registered."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    sources = [
        root / ".github/actions/wow-claude-agent/action.yml",
        root / ".github/workflows/wow-v17-engineering-provider-dispatcher.yml",
        root / ".github/workflows/wow-v17-claude-engineering-worker.yml",
        root / ".github/workflows/wow-v17-chatgpt-engineering-worker.yml",
    ]
    registry = (root / "artifacts/wow-engine/docs/failure_codes.md").read_text()
    codes = ("CLAUDE_STRUCTURED_OUTPUT_MISSING", "CLAUDE_STRUCTURED_OUTPUT_INVALID_JSON", "TARGET_INCIDENT_INVALID",
             "P0_DOMAIN_LEASE_MISSING", "STANDARD_TARGET_MUST_USE_GLOBAL_LEASE", "TARGET_INCIDENT_NOT_SUPPORTED")
    emitted = "\n".join(p.read_text() for p in sources)
    for code in codes:
        assert code in emitted, code
        assert re.search(rf"^\| `{code}` \|", registry, re.M), code
