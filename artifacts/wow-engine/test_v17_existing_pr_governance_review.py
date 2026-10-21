from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import textwrap
from datetime import timedelta
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/wow-v17-existing-pr-governance-review.yml"
VERIFIER = ROOT / ".github/scripts/verify_existing_pr_governance_receipt.py"
GATE = ROOT / ".github/workflows/wow-v17-trusted-governance-gate.yml"
MORNING_GREEN = ROOT / ".github/workflows/wow-v17-morning-green-continuation.yml"
RELEASE = ROOT / ".github/workflows/wow-v17-release-production-verification-agent.yml"


def _load_verifier():
    spec = importlib.util.spec_from_file_location("verify_existing_pr_governance_receipt", VERIFIER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _provenance_guard(workflow: Path, step_name: str, start: str, end: str) -> str:
    text = workflow.read_text(encoding="utf-8")
    step = text.split(f"      - name: {step_name}\n", 1)[1]
    script = textwrap.dedent(step.split("        run: |\n", 1)[1].split("\n      - name:", 1)[0])
    return start + script.split(start, 1)[1].split(end, 1)[0]


@pytest.mark.parametrize(
    ("workflow", "step_name", "start", "end"),
    [
        (
            MORNING_GREEN,
            "Verify trusted exact-head governance receipt",
            "trusted_worker=false\n",
            "zip_path=",
        ),
        (
            RELEASE,
            "Verify governance-producing workflow provenance",
            "protected_main_source=false\n",
            'echo "name=$name"',
        ),
    ],
)
def test_downstream_consumers_accept_only_successful_protected_workflow_run_receipts(
    workflow, step_name, start, end, tmp_path
):
    guard = "set -euo pipefail\n" + _provenance_guard(workflow, step_name, start, end)
    env = {
        "GITHUB_REPOSITORY": "gregoryharper84-ship-it/WOW-Dashboard",
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"),
        "GITHUB_OUTPUT": str(tmp_path / "output"),
        "name": "wow-v17-existing-pr-governance-review",
        "path": ".github/workflows/wow-v17-existing-pr-governance-review.yml",
        "event": "workflow_run",
        "head_branch": "main",
        "head_repo": "gregoryharper84-ship-it/WOW-Dashboard",
        "status": "completed",
        "conclusion": "success",
    }

    accepted = subprocess.run(
        ["bash", "-c", guard], env=os.environ | env,
        capture_output=True, text=True, check=False,
    )
    assert accepted.returncode == 0, accepted.stderr

    for field, value in (
        ("head_branch", "feature"),
        ("head_repo", "attacker/fork"),
        ("status", "in_progress"),
        ("conclusion", "failure"),
        ("event", "push"),
    ):
        rejected = subprocess.run(
            ["bash", "-c", guard], env=os.environ | (env | {field: value}),
            capture_output=True, text=True, check=False,
        )
        assert rejected.returncode != 0, (workflow, field, value)


def test_schema_one_worker_receipt_is_not_rejected_by_existing_pr_schema_guard(tmp_path):
    workflow = GATE.read_text(encoding="utf-8")
    step = workflow.split("      - name: Download and verify exact-head receipt\n", 1)[1]
    script = textwrap.dedent(step.split("        run: |\n", 1)[1].split("\n      - name:", 1)[0])
    guard = "cert_mode=$(" + script.split("cert_mode=$(", 1)[1].split(
        'verifier=".github/scripts/verify_engineering', 1
    )[0]
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps({
        "receipt_schema_version": 1,
        "certification_mode": "ORDINARY_ENGINEERING",
    }), encoding="utf-8")

    def run(workflow_name):
        return subprocess.run(
            ["bash", "-c", 'set -euo pipefail\nreceipt="$RECEIPT"\n' + guard],
            env=os.environ | {
                "RECEIPT": str(receipt),
                "WORKFLOW_NAME": workflow_name,
                "RUNNER_TEMP": str(tmp_path),
            },
            capture_output=True, text=True, check=False,
        )

    worker = run("wow-v17-chatgpt-engineering-worker")
    assert worker.returncode == 0, worker.stderr
    existing_pr_certifier = run("wow-v17-existing-pr-governance-review")
    assert existing_pr_certifier.returncode != 0
    assert "GOVERNANCE_RECEIPT_SCHEMA_UNSUPPORTED" in existing_pr_certifier.stderr


def test_existing_pr_certifier_is_protected_main_read_only_and_same_repo_only():
    text = WORKFLOW.read_text(encoding="utf-8")
    data = yaml.safe_load(text)

    assert "pull_request_target:" in text
    assert "workflow_dispatch:" in text
    assert "ref: main" in text
    assert "persist-credentials: false" in text
    assert "contents: read" in text
    assert "contents: write" not in text
    assert "pull-requests: read" in text
    assert "pull-requests: write" not in text
    assert '[ "$head_repo" = "$GITHUB_REPOSITORY" ]' in text
    assert "gh pr diff" not in text
    assert "TRUSTED_REVIEWERS_JSON: ${{ vars.WOW_V17_INDEPENDENT_REVIEWERS }}" in text
    assert "ANTHROPIC_API_KEY" not in text
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in text
    assert data["name"] == "wow-v17-existing-pr-governance-review"


def test_existing_pr_ci_selector_compiles_and_matches_exact_workflow_path():
    jq = shutil.which("jq")
    if jq is None:
        pytest.skip("jq is required by the GitHub Actions runner contract")

    selector = '[.[] | select(.name == $name and .path == $path)] | sort_by(.created_at) | reverse | first | "\\(.status)|\\(.conclusion // "")"'
    payload = '[{"name":"wow-verify","path":".github/workflows/wow-verify.yml","created_at":"2026-10-03T16:00:00Z","status":"completed","conclusion":"success"}]'
    proc = subprocess.run(
        [jq, "-r", "--arg", "name", "wow-verify", "--arg", "path", ".github/workflows/wow-verify.yml", selector],
        input=payload,
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "completed|success"


def test_existing_pr_certifier_waits_for_source_pinned_exact_head_ci():
    text = WORKFLOW.read_text(encoding="utf-8")
    for required in ("wow-verify", "wow-engine-verify", "wow-v17-rapid-repair", "wow-v17-change-impact-gate", "wow-v17-engineering-auditor-code-health", "wow-v17-spread-forward-shadow", "wow-v17-release-production-verification-agent"):
        assert required in text
    assert "for _ in $(seq 1 120); do" in text
    assert 'api_array "/repos/${GITHUB_REPOSITORY}/actions/runs?head_sha=${HEAD_SHA}"' in text
    assert "EXISTING_PR_REQUIRED_CI_FAILED" in text
    assert "EXISTING_PR_REQUIRED_CI_INCOMPLETE_AFTER_BOUNDED_WAIT" in text
    assert "EXISTING_PR_TRUST_ROOT_CHANGE_REQUIRES_WORKER_OR_BOOTSTRAP" not in text
    assert "--classify" in text
    assert "--bootstrap" in text
    assert "TRUST_ROOT_BOOTSTRAP" in text
    assert "ORDINARY_ENGINEERING" in text
    assert "EXISTING_PR_BOOTSTRAP_CHECKS_INCOMPLETE" in text
    assert "pull_request:" not in text.replace("pull_request_target:", "")
    assert ".github/scripts/verify_existing_pr_governance_receipt.py" in text
    assert 'select(.name == $name and .path == $path)' in text
    assert '\\(.conclusion // "")' in text
    assert '\\(.conclusion // \\"\\")' not in text


def test_existing_pr_collector_preserves_full_diff_provenance():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "{filename,previous_filename,status}" in text
    assert "EXISTING_PR_API_INVENTORY_LIMIT_EXCEEDED" in text
    assert "EXISTING_PR_HEAD_MOVED_DURING_CAPTURE" in text
    assert "--classify" in text


def test_existing_pr_certifier_uses_independent_review_evidence_not_synthetic_roles():
    text = WORKFLOW.read_text(encoding="utf-8")
    for role in ("ENGINEERING_LEAD_AGENT", "RESEARCH_TRIAGE_AGENT", "ENGINEERING_AGENT",
                 "INDEPENDENT_REVIEW_AGENT", "SYSTEM_ARCHITECT_AGENT", "QA_VERIFICATION_AGENT"):
        assert role not in text
    verifier_text = (ROOT / ".github/scripts/verify_existing_pr_governance_receipt.py").read_text()
    assert "REVIEW_EVIDENCE_VERIFIED" in verifier_text
    assert 'review.get("state") != "APPROVED"' in verifier_text
    assert "checks: read" in text


def test_existing_pr_certifier_emits_truthful_exact_head_receipt():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "wow-v17-engineering-governance-${{ steps.target.outputs.head_sha }}" in text
    assert "--pr-number \"$PR_NUMBER\"" in text
    assert "{filename,previous_filename,status}" in text
    verifier = _load_verifier()
    assert set(verifier.REQUIRED_CHECKS) == {
        "WOW required-three regression",
        "WOW governed probability backend",
        "WOW additional required regression",
    }
    assert "WOW Independent QA exact-head" not in verifier.REQUIRED_CHECKS
    assert "WOW Release Authority exact-head" not in verifier.REQUIRED_CHECKS
    assert verifier.REVIEW_RECEIPT_MAX_AGE == timedelta(minutes=30)
    assert verifier.FALLBACK_PROVIDER_SUNSET_UTC == "2026-11-01T00:00:00Z"
    assert "INDEPENDENT_REVIEWER_ALLOWLIST_MISSING" in (
        ROOT / ".github/scripts/verify_existing_pr_governance_receipt.py"
    ).read_text(encoding="utf-8")


def test_existing_pr_receipt_contract_runs_from_the_candidate_checkout():
    """PR CI must execute the receipt contract. The producer checks out protected main."""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--noconftest",
         str(ROOT / ".github/scripts/tests/test_existing_pr_governance_receipt.py")],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr



def test_release_verifier_jq_selectors_compile_and_match():
    jq = shutil.which("jq")
    if jq is None:
        pytest.skip("jq is required by the GitHub Actions runner contract")

    workflow_selector = '[.[] | select(.name == $name and .path == $path)] | sort_by(.created_at) | reverse | first | "\(.status)|\(.conclusion // "")"'
    workflow_payload = '[{"name":"wow-verify","path":".github/workflows/wow-verify.yml","created_at":"2026-10-03T16:00:00Z","status":"completed","conclusion":"success"}]'
    workflow_proc = subprocess.run(
        [jq, "-r", "--arg", "name", "wow-verify", "--arg", "path", ".github/workflows/wow-verify.yml", workflow_selector],
        input=workflow_payload,
        text=True,
        capture_output=True,
        check=False,
    )
    assert workflow_proc.returncode == 0, workflow_proc.stderr
    assert workflow_proc.stdout.strip() == "completed|success"

    gate_selector = '[.[] | select(.name == "Trusted exact-head engineering governance")] | sort_by(.started_at // .created_at) | reverse | first | "\(.status)|\(.conclusion // "")"'
    gate_payload = '[{"name":"Trusted exact-head engineering governance","started_at":"2026-10-03T16:00:00Z","status":"completed","conclusion":"success"}]'
    gate_proc = subprocess.run(
        [jq, "-r", gate_selector],
        input=gate_payload,
        text=True,
        capture_output=True,
        check=False,
    )
    assert gate_proc.returncode == 0, gate_proc.stderr
    assert gate_proc.stdout.strip() == "completed|success"

    release = RELEASE.read_text(encoding="utf-8")
    assert workflow_selector in release
    assert gate_selector in release
    assert r'\(.conclusion // \\"\\"' not in release


def test_release_verifier_does_not_require_itself_as_prerequisite():
    release = RELEASE.read_text(encoding="utf-8")
    required_block = release.split("required=(", 1)[1].split(")", 1)[0]
    assert "wow-v17-release-production-verification-agent" not in required_block
    for required in (
        "wow-verify",
        "wow-engine-verify",
        "wow-v17-rapid-repair",
        "wow-v17-change-impact-gate",
        "wow-v17-engineering-auditor-code-health",
        "wow-v17-spread-forward-shadow",
    ):
        assert required in required_block


def test_trusted_consumers_use_dedicated_existing_pr_verifier():
    gate = GATE.read_text(encoding="utf-8")
    morning = MORNING_GREEN.read_text(encoding="utf-8")
    release = RELEASE.read_text(encoding="utf-8")

    for text in (gate, morning, release):
        assert "wow-v17-existing-pr-governance-review.yml" in text
        assert "verify_existing_pr_governance_receipt.py" in text
        assert "wow-v17-existing-pr-governance-review" in text

    assert "pull_request_target) ;;" in gate
    assert "protected_main_source=true" in morning
    assert "protected_main_source=true" in release
    assert '"wow-verify|.github/workflows/wow-verify.yml"' in release
    assert '"wow-engine-verify|.github/workflows/wow-engine-verify.yml"' in release
    assert '"wow-v17-rapid-repair|.github/workflows/wow-v17-rapid-repair.yml"' in release
    assert '"wow-v17-change-impact-gate|.github/workflows/wow-v17-change-impact-gate.yml"' in release
    assert '"wow-v17-engineering-auditor-code-health|.github/workflows/wow-v17-engineering-auditor-code-health.yml"' in release
    assert '"wow-v17-spread-forward-shadow|.github/workflows/wow-v17-spread-forward-shadow.yml"' in release
    assert "Trusted exact-head engineering governance" in release
    assert "Lead/Triage/Engineering/Independent Review/QA governance receipt" not in release
    assert "active independent allowlisted APPROVED review" in release
    for text in (gate, morning, release):
        assert "--live-checks-json" in text
        assert "TRUST_ROOT_BOOTSTRAP" in text
