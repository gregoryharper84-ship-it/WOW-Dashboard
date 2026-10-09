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
    """Return a step's body: every line until the next line indented at step level or less."""
    source = WORKFLOW.read_text()
    lines = []
    for line in source.split(name_line, 1)[1].splitlines():
        if line.strip() and len(line) - len(line.lstrip()) <= 6:
            break
        lines.append(line)
    return "\n".join(lines) + "\n"


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
    expected = "BLOCKED_WITH_EXACT_REASON" if code == "ACTIONABLE_REPAIR_POLICY_BOUNDARY" else "UNRESOLVED_TYPED_FAILURE"
    assert outputs["disposition"] == expected
    assert outputs["next_action"] and outputs["revisit_trigger"]


def test_policy_boundary_reenters_only_by_protected_authorization(tmp_path):
    _, outputs = _run(tmp_path, TRIAGE_RISK="R3")
    assert "no automatic retry or provider failover" in outputs["next_action"]
    assert outputs["revisit_trigger"] == "Protected reviewer/owner authorization change only."


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
    assert {k: outputs[k] for k in ("code", "delivery", "pr", "disposition")} == {
        "code": "NONE", "delivery": "PR_READY", "pr": "1544", "disposition": "PR_CREATED",
    }
    assert "PR #1544" in (tmp_path / "summary.txt").read_text()


def test_draft_pr_passes_but_is_labelled_gates_failed(tmp_path):
    result, outputs = _run(tmp_path, prs=[{"number": 1544, "isDraft": True, "headRefOid": SHA}])
    assert result.returncode == 0, result.stdout + result.stderr
    assert outputs["delivery"] == "DRAFT_PR_GATES_FAILED"
    assert "does not authorize merge or deploy" in outputs["next_action"]


# --- durable persistence: execute the real persist and receipt Bash/jq ------

PERSIST = "      - name: Persist dual-stream heartbeat and receipt\n"
RECEIPT = "      - name: Append incident delivery receipt\n"
GH_STUB = r"""
gh() {
  printf '%s\n' "$*" >> "$GH_CALLS"
  if [[ " $* " == *" --method "* ]]; then
    for a in "$@"; do case "$a" in body=*) printf '%s' "${a#body=}" > "$GH_BODY";; esac; done
    return "${STUB_POST_RC:-0}"
  fi
  [ "${STUB_GET_RC:-0}" = "0" ] || return "$STUB_GET_RC"
  if [[ "$*" == *"--jq"* ]]; then printf '%s\n' "${STUB_OWNER:-}"; else printf '[]\n'; fi
}
"""


def _script(name_line):
    return dedent(_step(name_line).split("        run: |\n", 1)[1])


def _exec(tmp_path, name_line, env_overrides):
    env = dict(os.environ)
    env.update(
        {
            "RUNNER_TEMP": str(tmp_path),
            "GITHUB_RUN_ID": "777",
            "GITHUB_RUN_ATTEMPT": "2",
            "GITHUB_REPOSITORY": "owner/repo",
            "GITHUB_REPOSITORY_OWNER": "owner",
            "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.txt"),
            "GH_CALLS": str(tmp_path / "gh_calls.txt"),
            "GH_BODY": str(tmp_path / "gh_body.txt"),
        }
    )
    env.update(env_overrides)
    for f in ("gh_calls.txt", "gh_body.txt"):
        (tmp_path / f).unlink(missing_ok=True)
    result = subprocess.run(
        ["bash", "-c", GH_STUB + _script(name_line)],
        env=env, text=True, capture_output=True, check=False,
    )
    body = (tmp_path / "gh_body.txt").read_text() if (tmp_path / "gh_body.txt").exists() else ""
    return result, body


@pytest.mark.parametrize(
    "env, delivery, code, disposition",
    [
        (
            {"LEAD_ACTION": "REPAIR", "DELIVERY": "FAILED", "DELIVERY_CODE": "ACTIONABLE_REPAIR_POLICY_BOUNDARY",
             "DELIVERY_DISPOSITION": "BLOCKED_WITH_EXACT_REASON", "DELIVERY_OUTCOME": "failure"},
            "FAILED", "ACTIONABLE_REPAIR_POLICY_BOUNDARY", "BLOCKED_WITH_EXACT_REASON",
        ),
        (
            {"LEAD_ACTION": "REPAIR", "DELIVERY": "PR_READY", "DELIVERY_CODE": "NONE",
             "DELIVERY_DISPOSITION": "PR_CREATED", "DELIVERY_OUTCOME": "success"},
            "PR_READY", "NONE", "PR_CREATED",
        ),
        (
            {"LEAD_ACTION": "REPAIR", "DELIVERY_OUTCOME": "skipped"},
            "FAILED", "ACTIONABLE_REPAIR_DELIVERY_UNVERIFIED:skipped", "UNRESOLVED_TYPED_FAILURE",
        ),
        (
            {"LEAD_ACTION": "NO_ACTION"},
            "NOT_APPLICABLE", "NONE", "NOT_APPLICABLE",
        ),
    ],
)
def test_persist_step_builds_receipt_json_and_heartbeat(tmp_path, env, delivery, code, disposition):
    result, heartbeat = _exec(tmp_path, PERSIST, env)
    assert result.returncode == 0, result.stderr
    receipt = json.loads((tmp_path / "wow-dual-stream-receipt.json").read_text())
    assert receipt["restoration_delivery"] == delivery
    assert receipt["restoration_delivery_code"] == code
    assert receipt["restoration_disposition"] == disposition
    assert receipt["can_execute"] is False
    assert f"- restoration_delivery_code: {code}" in heartbeat
    assert f"- restoration_disposition: {disposition}" in heartbeat


def test_jq_args_declared_in_the_step_that_uses_them():
    persist = _step(PERSIST)
    for var in ("delivery", "delivery_code", "delivery_disposition"):
        assert f"--arg {var} " in persist
    source = WORKFLOW.read_text()
    assert source.count("--arg delivery_code ") == 1


POLICY_RECEIPT_ENV = {
    "INCIDENT": "1021", "DELIVERY": "FAILED", "CODE": "ACTIONABLE_REPAIR_POLICY_BOUNDARY",
    "DISPOSITION": "BLOCKED_WITH_EXACT_REASON",
    "NEXT_ACTION": "Protected reviewer/owner authorization change for risk R3; no automatic retry or provider failover.",
    "REVISIT": "Protected reviewer/owner authorization change only.", "DELIVERY_OUTCOME": "failure",
    "PR": "", "HEAD": "", "TRIAGE_RISK": "R3", "LEASE_GROUP": "ENGINEERING", "LEASE_EPOCH": "41",
    "WORKER_ID": "claude-777", "STUB_OWNER": "gregoryharper84-ship-it",
}


def test_incident_receipt_is_append_only_and_carries_exact_identity(tmp_path):
    result, body = _exec(tmp_path, RECEIPT, POLICY_RECEIPT_ENV)
    assert result.returncode == 0, result.stdout + result.stderr
    calls = (tmp_path / "gh_calls.txt").read_text()
    assert "--method POST /repos/owner/repo/issues/1021/comments" in calls
    assert "PATCH" not in calls
    for line in (
        "<!-- WOW_REPAIR_DELIVERY_RECEIPT:run=777:attempt=2 -->",
        "- incident: #1021",
        "- disposition: BLOCKED_WITH_EXACT_REASON",
        "- delivery_code: ACTIONABLE_REPAIR_POLICY_BOUNDARY",
        "- accountable_owner: gregoryharper84-ship-it",
        "- revisit_trigger: Protected reviewer/owner authorization change only.",
        "- lease_epoch: 41",
        "- worker_id: claude-777",
        "- independent_qa_approval: false",
        "- merge_or_deploy_authorized: false",
        "- can_execute: false",
    ):
        assert line in body.splitlines(), line


def test_incident_receipt_owner_falls_back_to_repository_owner(tmp_path):
    _, body = _exec(tmp_path, RECEIPT, {**POLICY_RECEIPT_ENV, "STUB_OWNER": ""})
    assert "- accountable_owner: owner" in body.splitlines()


def test_incident_receipt_records_unverified_gate(tmp_path):
    env = {**POLICY_RECEIPT_ENV, "CODE": "", "DISPOSITION": "", "DELIVERY_OUTCOME": "cancelled"}
    result, body = _exec(tmp_path, RECEIPT, env)
    assert result.returncode == 0
    assert "- delivery_code: ACTIONABLE_REPAIR_DELIVERY_UNVERIFIED:cancelled" in body
    assert "- disposition: UNRESOLVED_TYPED_FAILURE" in body


@pytest.mark.parametrize(
    "env, code",
    [
        ({"INCIDENT": "1021;id"}, "ACTIONABLE_REPAIR_RECEIPT_INCIDENT_INVALID"),
        ({"INCIDENT": ""}, "ACTIONABLE_REPAIR_RECEIPT_INCIDENT_INVALID"),
        ({"STUB_GET_RC": "1"}, "ACTIONABLE_REPAIR_RECEIPT_PERSIST_FAILED"),
        ({"STUB_POST_RC": "1"}, "ACTIONABLE_REPAIR_RECEIPT_PERSIST_FAILED"),
    ],
)
def test_incident_receipt_failures_are_typed(tmp_path, env, code):
    result, _ = _exec(tmp_path, RECEIPT, {**POLICY_RECEIPT_ENV, **env})
    assert result.returncode != 0
    assert f"::error::{code}:" in result.stdout


def test_receipt_cannot_be_built_by_agent_authored_code():
    source = WORKFLOW.read_text()
    assert source.index("      - name: Checkout implementation branch") < source.index(RECEIPT)
    script = _step(RECEIPT).split("run: |", 1)[1]
    for forbidden in ("${{", "python", "artifacts/", "./", "source "):
        assert forbidden not in script, forbidden
    assert "if: always() && steps.lead.outputs.action == 'REPAIR'" in _step(RECEIPT)


# --- dispatcher classification ---------------------------------------------


def _classifier():
    import importlib.util
    import sys

    path = WORKFLOW.parents[2] / "artifacts/wow-engine/v17/engineering_provider_failover.py"
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("engineering_provider_failover_gate", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.classify_provider_failure


def _failed_log(code):
    # Real failed-step logs echo the whole step script first, so every code name appears in it.
    return _delivery_shell() + f"\n##[error]{code}: incident=1021; detail.\n"


@pytest.mark.parametrize(
    "code, disposition",
    [
        ("ACTIONABLE_REPAIR_POLICY_BOUNDARY", "BLOCKED_WITH_EXACT_REASON"),
        ("ACTIONABLE_REPAIR_IMPLEMENTATION_FAILED", "UNRESOLVED_TYPED_FAILURE"),
        ("ACTIONABLE_REPAIR_PR_LOOKUP_FAILED", "UNRESOLVED_TYPED_FAILURE"),
        ("ACTIONABLE_REPAIR_RECEIPT_PERSIST_FAILED", "UNRESOLVED_TYPED_FAILURE"),
    ],
)
def test_dispatcher_classifies_emitted_code_not_script_text(code, disposition):
    result = _classifier()("anthropic", _failed_log(code)).as_dict()
    assert result["code"] == code
    assert result["disposition"] == disposition
    assert result["failover_eligible"] is False
    assert result["can_execute"] is False


def test_policy_boundary_never_fails_over_even_with_provider_noise():
    log = _failed_log("ACTIONABLE_REPAIR_POLICY_BOUNDARY") + "\nError: insufficient_quota rate limit exceeded\n"
    for provider in ("openai", "anthropic"):
        result = _classifier()(provider, log)
        assert result.code == "ACTIONABLE_REPAIR_POLICY_BOUNDARY"
        assert result.failover_eligible is False
        assert result.circuit_breaker_minutes == 0


def test_genuine_provider_outage_behind_implementation_failure_keeps_failover():
    log = "##[error]ACTIONABLE_REPAIR_IMPLEMENTATION_FAILED: x\ninsufficient_quota\n"
    result = _classifier()("openai", log)
    assert result.code == "ACTIONABLE_REPAIR_IMPLEMENTATION_FAILED"
    assert result.provider_signal == "OPENAI_API_QUOTA_EXCEEDED"
    assert result.failover_eligible is True


def test_script_text_alone_is_not_a_typed_outcome():
    result = _classifier()("anthropic", _delivery_shell())
    assert not result.code.startswith("ACTIONABLE_REPAIR_")


# --- Addendum section 4: every emitted code is registered ---------------------


def test_every_emitted_repair_code_is_registered_and_vice_versa():
    import re

    root = WORKFLOW.parents[2]
    emitted = set(re.findall(r"ACTIONABLE_REPAIR_[A-Z_]+", WORKFLOW.read_text()))
    emitted |= set(re.findall(
        r"ACTIONABLE_REPAIR_[A-Z_]+",
        (root / "artifacts/wow-engine/v17/engineering_provider_failover.py").read_text(),
    ))
    registry = (root / "artifacts/wow-engine/docs/failure_codes.md").read_text()
    registered = set(re.findall(r"^\| `(ACTIONABLE_REPAIR_[A-Z_]+)` \|", registry, re.M))
    assert emitted == registered
    assert "| `ACTIONABLE_REPAIR_POLICY_BOUNDARY` | engineering governance |" in registry
