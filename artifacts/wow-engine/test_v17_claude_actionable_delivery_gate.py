"""Regression for fail-closed Claude engineering repair delivery (incidents #1021, #1527).

Executes the protected worker's actual delivery-gate Bash against a deterministic
``gh`` stub. No provider is invoked, no source governance is altered and no real
PR is created.
"""
import json
import os
import subprocess
from pathlib import Path
from textwrap import dedent

import pytest

WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/wow-v17-claude-engineering-worker.yml"
STEP = "      - name: Enforce actionable repair delivery\n"
SHA = "a" * 40
BRANCH = "claude/engineering/123456-1"


def _step(name_line):
    source = WORKFLOW.read_text()
    body = source.split(name_line, 1)[1]
    return body.split("\n      - name: ", 1)[0]


def _delivery_shell():
    """Extract the actual workflow Bash instead of reimplementing the condition."""
    return dedent(_step(STEP).split("        run: |\n", 1)[1])


# --- structure -------------------------------------------------------------


def test_gate_only_runs_for_repair_and_never_for_legitimate_no_pr_actions():
    gate = _step(STEP)
    assert "if: always() && steps.lead.outputs.action == 'REPAIR'" in gate
    script = gate.split("run: |", 1)[1]
    assert "NO_ACTION" not in script
    assert "VERIFY_RELEASE" not in script


def test_gate_runs_after_pr_attempt_and_before_durable_persistence():
    source = WORKFLOW.read_text()
    pr = source.index("      - name: Open governed engineering PR")
    gate = source.index(STEP)
    persist = source.index("      - name: Persist dual-stream heartbeat and receipt")
    assert pr < gate < persist


def test_typed_delivery_outcome_is_persisted_to_heartbeat_and_receipt():
    persist = _step("      - name: Persist dual-stream heartbeat and receipt\n")
    assert "DELIVERY_CODE: ${{ steps.delivery.outputs.code }}" in persist
    assert "restoration_delivery_code: $delivery_code" in persist
    assert "- restoration_delivery_code: ${DELIVERY_CODE:-NONE}" in persist
    assert "ACTIONABLE_REPAIR_DELIVERY_UNVERIFIED" in persist
    receipt = _step("      - name: Record team receipt\n")
    assert "steps.delivery.outputs.code" in receipt


def test_gate_passes_untrusted_values_through_env_not_inline_expressions():
    script = _step(STEP).split("run: |", 1)[1]
    assert "${{" not in script


# --- behaviour -------------------------------------------------------------

HEALTHY = {
    "TRIAGE_OUTCOME": "success",
    "TRIAGE_REPAIRABLE": "true",
    "TRIAGE_RISK": "R1",
    "SPECIALIST_HYPOTHESIS": "CONFIRMED",
    "MUTATION_GATE_OUTCOME": "success",
    "IMPLEMENTATION_OUTCOME": "success",
    "IMPL_OUTCOME": "success",
    "CHANGED": "true",
    "BRANCH": BRANCH,
    "EXPECTED_HEAD": SHA,
}


def _run(tmp_path, *, prs=None, gh_rc=0, **overrides):
    out = tmp_path / "output.txt"
    summary = tmp_path / "summary.txt"
    for f in (out, summary):
        f.unlink(missing_ok=True)
    env = dict(os.environ)
    env.update(HEALTHY)
    env.update(overrides)
    env.update(
        {
            "INCIDENT": "1021",
            "GITHUB_REPOSITORY": "owner/repo",
            "GITHUB_OUTPUT": str(out),
            "GITHUB_STEP_SUMMARY": str(summary),
            "STUB_PRS": json.dumps(
                prs if prs is not None else [{"number": 1544, "isDraft": False, "headRefOid": SHA}]
            ),
            "STUB_RC": str(gh_rc),
        }
    )
    # Replace only the gh CLI with a deterministic local stub.
    stub = (
        'gh() { [ "$STUB_RC" = "0" ] || { echo "HTTP 502" >&2; return "$STUB_RC"; }; '
        'printf "%s\\n" "$STUB_PRS"; }\n'
    )
    result = subprocess.run(
        ["bash", "-c", stub + _delivery_shell()],
        env=env, text=True, capture_output=True, check=False,
    )
    lines = out.read_text().splitlines() if out.exists() else []
    outputs = dict(line.split("=", 1) for line in lines if "=" in line)
    return result, outputs


@pytest.mark.parametrize(
    "overrides, code",
    [
        ({"TRIAGE_OUTCOME": "skipped"}, "ACTIONABLE_REPAIR_TRIAGE_FAILED"),
        ({"TRIAGE_RISK": "R2-repair-policy"}, "ACTIONABLE_REPAIR_POLICY_BOUNDARY"),
        ({"TRIAGE_RISK": "R3"}, "ACTIONABLE_REPAIR_POLICY_BOUNDARY"),
        ({"TRIAGE_REPAIRABLE": "false"}, "ACTIONABLE_REPAIR_TRIAGE_NOT_REPAIRABLE"),
        ({"SPECIALIST_HYPOTHESIS": "REJECTED"}, "ACTIONABLE_REPAIR_HYPOTHESIS_UNCONFIRMED"),
        ({"TRIAGE_RISK": "NONE"}, "ACTIONABLE_REPAIR_RISK_UNRECOGNIZED"),
        ({"MUTATION_GATE_OUTCOME": "failure"}, "ACTIONABLE_REPAIR_MUTATION_DENIED"),
        ({"IMPLEMENTATION_OUTCOME": "failure"}, "ACTIONABLE_REPAIR_IMPLEMENTATION_FAILED"),
        ({"IMPL_OUTCOME": "failure"}, "ACTIONABLE_REPAIR_IMPLEMENTATION_FAILED"),
        ({"CHANGED": "false", "BRANCH": ""}, "ACTIONABLE_REPAIR_NO_DELIVERABLE"),
        ({"EXPECTED_HEAD": "missing"}, "ACTIONABLE_REPAIR_HEAD_INVALID"),
        ({"EXPECTED_HEAD": "A" * 40}, "ACTIONABLE_REPAIR_HEAD_INVALID"),
        ({"BRANCH": "main"}, "ACTIONABLE_REPAIR_BRANCH_INVALID"),
        ({"BRANCH": "claude/engineering/1-1;id"}, "ACTIONABLE_REPAIR_BRANCH_INVALID"),
    ],
)
def test_each_non_delivery_cause_fails_closed_with_its_own_typed_code(tmp_path, overrides, code):
    result, outputs = _run(tmp_path, **overrides)
    assert result.returncode != 0
    assert f"::error::{code}:" in result.stdout
    assert outputs["code"] == code
    assert outputs["delivery"] == "FAILED"


@pytest.mark.parametrize("overrides", [{"TRIAGE_RISK": "R3"}, {"TRIAGE_REPAIRABLE": "false"}])
def test_governed_denials_are_not_collapsed_into_no_deliverable(tmp_path, overrides):
    result, _ = _run(tmp_path, CHANGED="false", BRANCH="", **overrides)
    assert result.returncode != 0
    assert "ACTIONABLE_REPAIR_NO_DELIVERABLE" not in result.stdout


def test_pr_missing_fails_closed(tmp_path):
    result, outputs = _run(tmp_path, prs=[])
    assert result.returncode != 0
    assert outputs["code"] == "ACTIONABLE_REPAIR_PR_MISSING"


def test_ambiguous_pr_match_fails_closed(tmp_path):
    two = [{"number": 1, "isDraft": False, "headRefOid": SHA}, {"number": 2, "isDraft": False, "headRefOid": SHA}]
    result, outputs = _run(tmp_path, prs=two)
    assert result.returncode != 0
    assert outputs["code"] == "ACTIONABLE_REPAIR_PR_AMBIGUOUS"


def test_stale_pr_head_fails_closed(tmp_path):
    result, outputs = _run(tmp_path, prs=[{"number": 1544, "isDraft": False, "headRefOid": "b" * 40}])
    assert result.returncode != 0
    assert outputs["code"] == "ACTIONABLE_REPAIR_PR_HEAD_MISMATCH"


def test_github_outage_is_typed_not_silent(tmp_path):
    result, outputs = _run(tmp_path, gh_rc=1)
    assert result.returncode != 0
    assert outputs["code"] == "ACTIONABLE_REPAIR_PR_LOOKUP_FAILED"


def test_exact_head_ready_pr_passes(tmp_path):
    result, outputs = _run(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert outputs == {"code": "NONE", "delivery": "PR_READY", "pr": "1544"}
    assert "PR #1544" in (tmp_path / "summary.txt").read_text()


def test_draft_pr_passes_but_is_labelled_gates_failed(tmp_path):
    result, outputs = _run(tmp_path, prs=[{"number": 1544, "isDraft": True, "headRefOid": SHA}])
    assert result.returncode == 0, result.stdout + result.stderr
    assert outputs["delivery"] == "DRAFT_PR_GATES_FAILED"
