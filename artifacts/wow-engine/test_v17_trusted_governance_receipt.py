import importlib.util
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import zipfile
from datetime import datetime, timezone

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
VERIFIER_PATH = ROOT / ".github" / "scripts" / "verify_engineering_governance_receipt.py"
WORKER = ROOT / ".github" / "workflows" / "wow-v17-chatgpt-engineering-worker.yml"
CLAUDE_WORKER = ROOT / ".github" / "workflows" / "wow-v17-claude-engineering-worker.yml"
GATE = ROOT / ".github" / "workflows" / "wow-v17-trusted-governance-gate.yml"
MORNING_GREEN = ROOT / ".github" / "workflows" / "wow-v17-morning-green-continuation.yml"
RELEASE = ROOT / ".github" / "workflows" / "wow-v17-release-production-verification-agent.yml"
EXISTING_PR_REVIEW = ROOT / ".github" / "workflows" / "wow-v17-existing-pr-governance-review.yml"
EXISTING_PR_VERIFIER = ROOT / ".github" / "scripts" / "verify_existing_pr_governance_receipt.py"
SOURCE_REVIEW_VERIFIER = ROOT / ".github" / "scripts" / "independent_source_review_receipt.py"


def _load_verifier():
    spec = importlib.util.spec_from_file_location("verify_engineering_governance_receipt", VERIFIER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _load_source_review_verifier():
    spec = importlib.util.spec_from_file_location("independent_source_review_receipt_for_gate", SOURCE_REVIEW_VERIFIER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _receipt(
    workflow_name: str = "wow-v17-chatgpt-engineering-worker",
    branch: str = "chatgpt/engineering/12345-1",
):
    return {
        "receipt_schema_version": 1,
        "workflow_name": workflow_name,
        "repository": "owner/repo",
        "workflow_run_id": "12345",
        "implementation": {
            "changed": True,
            "branch": branch,
            "head_sha": "abc123",
            "incident_id": "1247",
            "risk_class": "R1",
        },
        "handoff": {
            "lead_dispatch": {"decision": "REPAIR"},
            "triage": {"repairable": True, "incident_id": "1247"},
            "specialist": {"name": "REPOSITORY_GOVERNANCE_AGENT"},
            "engineering": {"changed": True, "incident_id": "1247"},
            "independent_review": {"decision": "PASS"},
            "system_architect": {"decision": "NOT_APPLICABLE"},
            "deterministic_regression": {"status": "0"},
            "qa": {"decision": "PASS"},
        },
        "governance_complete": True,
        "terminal_authority": "V17_TERMINAL_REDUCER",
        "can_execute": False,
    }


@pytest.mark.parametrize(
    ("workflow_name", "branch"),
    [
        ("wow-v17-chatgpt-engineering-worker", "chatgpt/engineering/12345-1"),
        ("wow-v17-claude-engineering-worker", "claude/engineering/12345-1"),
    ],
)
def test_exact_head_receipt_passes_for_each_governed_provider(workflow_name, branch):
    verifier = _load_verifier()
    verifier.verify_receipt(
        _receipt(workflow_name=workflow_name, branch=branch),
        expected_repository="owner/repo",
        expected_head_sha="abc123",
        expected_head_ref=branch,
        expected_workflow_run_id="12345",
        expected_workflow_name=workflow_name,
    )


def test_receipt_workflow_name_must_match_producing_workflow():
    verifier = _load_verifier()
    receipt = _receipt(workflow_name="wow-v17-claude-engineering-worker", branch="claude/engineering/12345-1")
    with pytest.raises(verifier.GovernanceReceiptError, match="receipt workflow name does not match producing workflow"):
        verifier.verify_receipt(
            receipt,
            expected_repository="owner/repo",
            expected_head_sha="abc123",
            expected_head_ref="claude/engineering/12345-1",
            expected_workflow_run_id="12345",
            expected_workflow_name="wow-v17-chatgpt-engineering-worker",
        )


def test_untrusted_workflow_identity_fails_closed():
    verifier = _load_verifier()
    receipt = _receipt(workflow_name="forged-engineering-worker")
    with pytest.raises(verifier.GovernanceReceiptError, match="receipt workflow identity mismatch"):
        verifier.verify_receipt(
            receipt,
            expected_repository="owner/repo",
            expected_head_sha="abc123",
            expected_head_ref="chatgpt/engineering/12345-1",
            expected_workflow_run_id="12345",
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("head_sha", "stale-sha", "receipt head SHA mismatch"),
        ("branch", "forged/branch", "receipt branch mismatch"),
    ],
)
def test_stale_or_forged_implementation_identity_fails(field, value, message):
    verifier = _load_verifier()
    receipt = _receipt()
    receipt["implementation"][field] = value
    with pytest.raises(verifier.GovernanceReceiptError, match=message):
        verifier.verify_receipt(
            receipt,
            expected_repository="owner/repo",
            expected_head_sha="abc123",
            expected_head_ref="chatgpt/engineering/12345-1",
            expected_workflow_run_id="12345",
        )


def test_missing_independent_review_or_qa_fails_closed():
    verifier = _load_verifier()
    receipt = _receipt()
    receipt["handoff"]["independent_review"]["decision"] = "REJECT"
    with pytest.raises(verifier.GovernanceReceiptError, match="independent review did not pass"):
        verifier.verify_receipt(
            receipt,
            expected_repository="owner/repo",
            expected_head_sha="abc123",
            expected_head_ref="chatgpt/engineering/12345-1",
            expected_workflow_run_id="12345",
        )

    receipt = _receipt()
    receipt["handoff"]["qa"]["decision"] = "NOT_RUN"
    with pytest.raises(verifier.GovernanceReceiptError, match="QA verification did not pass"):
        verifier.verify_receipt(
            receipt,
            expected_repository="owner/repo",
            expected_head_sha="abc123",
            expected_head_ref="chatgpt/engineering/12345-1",
            expected_workflow_run_id="12345",
        )


def test_workflows_use_trusted_exact_head_artifact_not_pr_body_as_authority():
    worker = WORKER.read_text(encoding="utf-8")
    claude_worker = CLAUDE_WORKER.read_text(encoding="utf-8")
    gate = GATE.read_text(encoding="utf-8")
    morning_green = MORNING_GREEN.read_text(encoding="utf-8")

    for governed_worker in (worker, claude_worker):
        assert "wow-v17-engineering-governance-${{ steps.impl.outputs.head_sha }}" in governed_worker
        assert "independent_review: {decision: $review}" in governed_worker
        assert "qa: {decision: $qa}" in governed_worker
        assert 'echo "head_sha=$head_sha" >> "$GITHUB_OUTPUT"' in governed_worker

    assert '--arg workflow_name "wow-v17-chatgpt-engineering-worker"' in worker
    assert '--arg workflow_name "wow-v17-claude-engineering-worker"' in claude_worker

    assert "pull_request_target:" in gate
    assert "ref: main" in gate
    artifact_step = gate.split("- name: Resolve trusted exact-head governance artifact", 1)[1].split(
        "- name: Verify trusted workflow identity and completion", 1
    )[0]
    assert 'artifact_name="wow-v17-engineering-governance-${head_sha}"' in artifact_step
    assert '.name == $name' in artifact_step
    assert '[ "$(jq \'length\' <<<"$artifact_matches")" -eq 1 ]' in artifact_step
    assert '.name | test("^wow-v17-engineering-governance-[a-f0-9]{40}$")' in artifact_step
    assert '.event == "workflow_run"' in artifact_step
    assert '.head_branch == "main"' in artifact_step
    assert '.head_repository.full_name == $repo and .repository.full_name == $repo' in artifact_step
    assert '.status == "completed" and .conclusion == "success"' in artifact_step
    assert '.target.head_sha == $sha and .repository == $repo' in artifact_step
    assert '.workflow_run_id == ($run_id | tostring)' in artifact_step
    assert "GOVERNANCE_RECEIPT_CHECKSUM_MISMATCH" in artifact_step
    assert 'artifact_name="wow-v17-engineering-governance-${head_sha}"' in gate
    assert 'head_sha="$EVENT_HEAD_SHA"' in artifact_step
    assert '.state == "open" and .head.sha == $sha and .head.repo.full_name == $repo' in gate
    assert '.base.ref == "main" and .base.repo.full_name == $repo' in gate
    assert '--head-sha "$PR_HEAD_SHA"' in gate
    assert '--head-ref "$PR_HEAD_REF"' in gate
    assert "verify_engineering_governance_receipt.py" in gate
    assert "GOVERNANCE_RECEIPT_CHECKSUM_MISMATCH" in gate
    assert '[ "$head_branch" = "main" ]' in gate
    assert '[ "$head_repo" = "$GITHUB_REPOSITORY" ]' in gate
    assert "wow-v17-chatgpt-engineering-worker.yml" in gate
    assert "wow-v17-claude-engineering-worker.yml" in gate
    assert '[ "$event" = "workflow_dispatch" ]' in gate
    assert 'id: workflow' in gate
    assert 'echo "name=$name" >> "$GITHUB_OUTPUT"' in gate
    assert '--workflow-name "$WORKFLOW_NAME"' in gate
    identity_step = gate.split("- name: Verify trusted workflow identity and completion", 1)[1].split(
        "- name: Download and verify exact-head receipt", 1
    )[0]
    receipt_step = gate.split("- name: Download and verify exact-head receipt", 1)[1]
    assert "WORKFLOW_NAME:" not in identity_step
    assert "WORKFLOW_NAME: ${{ steps.workflow.outputs.name }}" in receipt_step

    assert "Verify trusted exact-head governance receipt" in morning_green
    assert 'protected_main_source=false' in morning_green
    assert '[ "$head_repo" != "$GITHUB_REPOSITORY" ]' in morning_green
    assert "wow-v17-chatgpt-engineering-worker.yml" in morning_green
    assert "wow-v17-claude-engineering-worker.yml" in morning_green
    assert "wow-v17-existing-pr-governance-review.yml" in morning_green
    assert "pull_request_target" in morning_green
    assert "verify_existing_pr_governance_receipt.py" in morning_green
    assert '--workflow-name "$name"' in morning_green
    assert "steps.governance.outputs.approved == 'true'" in morning_green
    assert "GOVERNANCE_REWORK" in morning_green


def _run_gate_artifact_resolution(tmp_path, *, event, artifacts, run=None, receipt=None, event_head=None, live_head=None):
    workflow = yaml.load(GATE.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    step = next(
        item for item in workflow["jobs"]["trusted-governance"]["steps"]
        if item.get("name") == "Resolve trusted exact-head governance artifact"
    )
    runner_temp = tmp_path / "runner-temp"
    runner_temp.mkdir()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "gh").write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys\n"
        "routes=json.loads(pathlib.Path(os.environ['FAKE_GH_ROUTES']).read_text())\n"
        "path=sys.argv[-1]\n"
        "value=routes[path]\n"
        "if isinstance(value, dict) and '__file' in value:\n"
        "    sys.stdout.buffer.write(pathlib.Path(value['__file']).read_bytes())\n"
        "else:\n"
        "    print(json.dumps(value))\n",
        encoding="utf-8",
    )
    (bindir / "gh").chmod(0o755)
    (bindir / "seq").write_text("#!/bin/sh\nprintf '1\\n'\n", encoding="utf-8")
    (bindir / "seq").chmod(0o755)
    (bindir / "sleep").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (bindir / "sleep").chmod(0o755)

    routes = {}
    env = {
        "EVENT_NAME": event,
        "EVENT_PR_NUMBER": "42" if event != "workflow_run" else "",
        "EVENT_HEAD_SHA": event_head or ("a" * 40),
        "EVENT_HEAD_REF": "fix/exact-head",
        "SOURCE_RUN_ID": "123" if event == "workflow_run" else "",
    }
    if event == "workflow_run":
        routes["/repos/owner/repo/actions/runs/123"] = run
        routes["/repos/owner/repo/actions/runs/123/artifacts?per_page=100"] = {
            "artifacts": artifacts,
        }
        zip_path = tmp_path / "receipt.zip"
        with zipfile.ZipFile(zip_path, "w") as archive:
            payload = json.dumps(receipt, sort_keys=True).encode()
            archive.writestr("wow-engineering-governance-receipt.json", payload)
            import hashlib
            archive.writestr(
                "wow-engineering-governance-receipt.sha256",
                hashlib.sha256(payload).hexdigest(),
            )
        routes["/repos/owner/repo/actions/artifacts/55/zip"] = {"__file": str(zip_path)}
    else:
        # New P1 stale-SHA guard always reads live PR identity before trusting
        # an artifact. The fixture must model that authenticated GitHub source.
        routes["/repos/owner/repo/pulls/42"] = {
            "head": {"sha": live_head or env["EVENT_HEAD_SHA"]},
        }
        routes[
            f"/repos/owner/repo/actions/artifacts?name=wow-v17-engineering-governance-{env['EVENT_HEAD_SHA']}&per_page=20"
        ] = {"artifacts": artifacts}
    routes_path = tmp_path / "routes.json"
    routes_path.write_text(json.dumps(routes), encoding="utf-8")
    output = tmp_path / "output"
    process_env = dict(
        os.environ,
        **env,
        GITHUB_REPOSITORY="owner/repo",
        GITHUB_OUTPUT=str(output),
        RUNNER_TEMP=str(runner_temp),
        FAKE_GH_ROUTES=str(routes_path),
        PATH=f"{bindir}:{os.environ['PATH']}",
    )
    proc = subprocess.run(
        ["bash", "-c", step["run"]],
        cwd=ROOT,
        env=process_env,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc, output.read_text(encoding="utf-8") if output.exists() else ""


def _certifier_fixture(*, run_overrides=None, artifact_sha=None, receipt_sha=None):
    sha_from_name = artifact_sha or ("a" * 40)
    target_sha = receipt_sha or sha_from_name
    run = {
        "id": 123,
        "name": "wow-v17-existing-pr-governance-review",
        "path": ".github/workflows/wow-v17-existing-pr-governance-review.yml",
        "event": "workflow_run",
        "head_branch": "main",
        "head_repository": {"full_name": "owner/repo"},
        "repository": {"full_name": "owner/repo"},
        "status": "completed",
        "conclusion": "success",
    }
    run.update(run_overrides or {})
    receipt = {
        "receipt_schema_version": 3,
        "repository": "owner/repo",
        "workflow_name": "wow-v17-existing-pr-governance-review",
        "workflow_run_id": "123",
        "target": {"pr_number": 42, "head_sha": target_sha, "head_ref": "fix/exact-head"},
    }
    artifact = {
        "id": 55,
        "name": f"wow-v17-engineering-governance-{sha_from_name}",
        "expired": False,
        "workflow_run": {"id": 123},
    }
    return run, receipt, artifact


def test_trusted_gate_consumes_authenticated_automatic_qa_review_run(tmp_path):
    source_review = _load_source_review_verifier()
    head = "a" * 40
    main_sha = "c" * 40
    paths = ["artifacts/wow-engine/v17/fixture.py"]
    files = [{
        "filename": paths[0],
        "previous_filename": None,
        "status": "modified",
        "patch": "@@ -1 +1 @@\n-old\n+new",
        "additions": 1,
        "deletions": 1,
    }]
    diff_sha = source_review.canonical_diff(files, expected_count=1)[1]
    qa_run = {
        "id": 456,
        "run_attempt": 2,
        "name": source_review.WORKFLOW_NAME,
        "workflow_id": source_review.WORKFLOW_ID,
        "path": source_review.WORKFLOW_PATH,
        "event": "workflow_run",
        "head_branch": "main",
        "repository": source_review.CANONICAL_REPOSITORY,
        "head_sha": main_sha,
    }
    review_artifact = source_review.build_artifact(
        repository=source_review.CANONICAL_REPOSITORY,
        pr={
            "number": 42,
            "state": "open",
            "draft": False,
            "merged_at": None,
            "changed_files": 1,
            "user": {"id": 9001, "login": "wow-engineering[bot]"},
            "head": {
                "sha": head,
                "ref": "fix/exact-head",
                "repo": {"full_name": source_review.CANONICAL_REPOSITORY},
            },
            "base": {
                "ref": "main",
                "repo": {"full_name": source_review.CANONICAL_REPOSITORY},
            },
        },
        files=files,
        commit={
            "sha": head,
            "commit": {"tree": {"sha": "d" * 40}, "message": "Fixture review"},
            "parents": [{"sha": "e" * 40}],
            "author": {"login": "copilot-swe-agent"},
            "committer": {"login": "copilot-swe-agent"},
        },
        review_result={
            "decision": "PASS",
            "summary": "Reviewed the exact source diff.",
            "reviewed_head_sha": head,
            "reviewed_diff_sha256": diff_sha,
            "reviewed_files": paths,
            "assessment": {
                "source_review": "PASS",
                "security_review": "PASS",
                "probability_integrity_review": "PASS",
            },
            "findings": [],
        },
        run=qa_run,
        now=datetime.now(timezone.utc),
    )
    artifact_path = tmp_path / "source-review.json"
    source_review.write_artifact(review_artifact, artifact_path)
    artifact_bytes = artifact_path.read_bytes()
    artifact_digest = hashlib.sha256(artifact_bytes).hexdigest()
    source_run = {
        **qa_run,
        "name": f"{source_review.WORKFLOW_NAME} event=workflow_run pr=AUTO",
        "workflow_id": source_review.WORKFLOW_ID,
        "head_repository": {"full_name": source_review.CANONICAL_REPOSITORY},
        "repository": {"full_name": source_review.CANONICAL_REPOSITORY},
        "status": "completed",
        "conclusion": "success",
    }
    artifact_name = (
        f"wow-v17-independent-source-review-42-{head}-456-2"
    )
    artifact_meta = {
        "id": 77,
        "name": artifact_name,
        "expired": False,
        "created_at": review_artifact["created_at_utc"],
        "workflow_run": {"id": 456},
    }
    receipt = {
        "receipt_schema_version": 3,
        "certification_mode": "ORDINARY_ENGINEERING",
        "target": {"pr_number": 42, "head_sha": head, "head_ref": "fix/exact-head"},
        "independent_review": {
            "artifact": review_artifact,
            "artifact_id": 77,
            "artifact_sha256": artifact_digest,
        },
    }
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    archive_path = tmp_path / "source-review.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.write(artifact_path, "wow-v17-independent-source-review.json")
        archive.writestr(
            "wow-v17-independent-source-review.json.sha256",
            artifact_digest + "\n",
        )
    routes_path = tmp_path / "routes.json"
    routes_path.write_text(json.dumps({
        f"/repos/{source_review.CANONICAL_REPOSITORY}/actions/runs/456": source_run,
        f"/repos/{source_review.CANONICAL_REPOSITORY}/actions/artifacts/77": artifact_meta,
        f"/repos/{source_review.CANONICAL_REPOSITORY}/actions/artifacts?per_page=100&page=1": {
            "artifacts": [artifact_meta],
        },
        f"/repos/{source_review.CANONICAL_REPOSITORY}/actions/artifacts/77/zip": {
            "__file": str(archive_path),
        },
    }), encoding="utf-8")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "gh").write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys\n"
        "routes=json.loads(pathlib.Path(os.environ['FAKE_GH_ROUTES']).read_text())\n"
        "value=routes[sys.argv[-1]]\n"
        "if isinstance(value, dict) and '__file' in value:\n"
        "    sys.stdout.buffer.write(pathlib.Path(value['__file']).read_bytes())\n"
        "else:\n"
        "    print(json.dumps(value))\n",
        encoding="utf-8",
    )
    (bindir / "gh").chmod(0o755)
    step = GATE.read_text(encoding="utf-8").split(
        "      - name: Download and verify exact-head receipt\n", 1
    )[1]
    script = textwrap.dedent(
        step.split("        run: |\n", 1)[1].split("\n      - name:", 1)[0]
    )
    consumer = (
        'set -euo pipefail\nreceipt="$RECEIPT"\n'
        "cert_mode=ORDINARY_ENGINEERING\nreceipt_schema=3\n"
        + "source_review_run="
        + script.split("source_review_run=", 1)[1].split(
            'elif [ "$WORKFLOW_NAME" = "wow-v17-existing-pr-governance-review" ]', 1
        )[0]
        + "fi\n"
    )
    env = os.environ | {
        "GITHUB_REPOSITORY": source_review.CANONICAL_REPOSITORY,
        "WORKFLOW_NAME": "wow-v17-existing-pr-governance-review",
        "RUNNER_TEMP": str(tmp_path / "runner"),
        "RECEIPT": str(receipt_path),
        "FAKE_GH_ROUTES": str(routes_path),
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "GH_TOKEN": "read-only",
    }
    Path(env["RUNNER_TEMP"]).mkdir()

    accepted = subprocess.run(
        ["bash", "-c", consumer], cwd=ROOT, env=env,
        capture_output=True, text=True, check=False,
    )
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr

    for field, value in (
        ("event", "pull_request_target"),
        ("head_branch", "feature"),
        ("status", "in_progress"),
        ("conclusion", "failure"),
    ):
        changed_run = source_run | {field: value}
        changed_routes = json.loads(routes_path.read_text(encoding="utf-8"))
        changed_routes[
            f"/repos/{source_review.CANONICAL_REPOSITORY}/actions/runs/456"
        ] = changed_run
        routes_path.write_text(json.dumps(changed_routes), encoding="utf-8")
        rejected = subprocess.run(
            ["bash", "-c", consumer], cwd=ROOT, env=env,
            capture_output=True, text=True, check=False,
        )
        assert rejected.returncode != 0, (field, value)
        assert "GOVERNANCE_SOURCE_REVIEW_RUN_UNTRUSTED" in rejected.stderr


def test_successful_certifier_without_receipt_exits_neutrally(tmp_path):
    run, receipt, _ = _certifier_fixture()
    proc, output = _run_gate_artifact_resolution(
        tmp_path, event="workflow_run", artifacts=[], run=run, receipt=receipt
    )

    assert proc.returncode == 0, proc.stderr
    assert output.splitlines() == ["applicable=false"]
    assert "GOVERNANCE_EVENT_NOT_APPLICABLE:CERTIFIER_PRODUCED_NO_RECEIPT" in proc.stdout


@pytest.mark.parametrize(
    "artifacts",
    [
        [{"id": 56, "name": "unrelated-artifact", "expired": False}],
        [{"id": 55, "name": "wow-v17-engineering-governance-" + "a" * 40, "expired": True}],
    ],
)
def test_certifier_with_unusable_artifacts_still_fails_closed(tmp_path, artifacts):
    run, receipt, _ = _certifier_fixture()
    proc, output = _run_gate_artifact_resolution(
        tmp_path, event="workflow_run", artifacts=artifacts, run=run, receipt=receipt
    )

    assert proc.returncode != 0
    assert "GOVERNANCE_CERTIFIER_RECEIPT_MISSING_OR_DUPLICATE" in proc.stderr
    assert "applicable" not in output


def test_untrusted_certifier_without_receipt_is_not_neutral(tmp_path):
    run, receipt, _ = _certifier_fixture(run_overrides={"conclusion": "failure"})
    proc, output = _run_gate_artifact_resolution(
        tmp_path, event="workflow_run", artifacts=[], run=run, receipt=receipt
    )

    assert proc.returncode != 0
    assert "GOVERNANCE_CERTIFIER_RUN_UNTRUSTED_OR_NOT_SUCCESSFUL" in proc.stderr
    assert "applicable" not in output


def test_trusted_gate_skips_failed_certifier_runs_and_gates_downstream_steps():
    workflow = yaml.load(GATE.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    job = workflow["jobs"]["trusted-governance"]

    assert "github.event.workflow_run.conclusion == 'success'" in job["if"]
    for step in job["steps"][2:]:
        assert step["if"].startswith("steps.artifact.outputs.applicable == 'true'"), step["name"]


@pytest.mark.parametrize(
    ("run_overrides", "artifact_sha", "receipt_sha", "reason"),
    [
        ({"event": "pull_request"}, None, None, "GOVERNANCE_CERTIFIER_RUN_UNTRUSTED_OR_NOT_SUCCESSFUL"),
        ({"head_repository": {"full_name": "fork/repo"}}, None, None,
         "GOVERNANCE_CERTIFIER_RUN_UNTRUSTED_OR_NOT_SUCCESSFUL"),
        (None, "b" * 40, "a" * 40, "GOVERNANCE_CERTIFIER_RECEIPT_TARGET_MISMATCH"),
    ],
)
def test_workflow_run_certifier_rejects_forged_or_stale_source(
    tmp_path, run_overrides, artifact_sha, receipt_sha, reason
):
    run, receipt, artifact = _certifier_fixture(
        run_overrides=run_overrides,
        artifact_sha=artifact_sha,
        receipt_sha=receipt_sha,
    )
    proc, _ = _run_gate_artifact_resolution(
        tmp_path,
        event="workflow_run",
        artifacts=[artifact],
        run=run,
        receipt=receipt,
    )

    assert proc.returncode != 0
    assert reason in proc.stderr


@pytest.mark.parametrize(
    ("artifact_name", "duplicate", "expected_success"),
    [
        ("wow-v17-engineering-governance-" + "a" * 40, False, True),
        ("wow-v17-engineering-governance-" + "b" * 40, False, False),
        ("wow-v17-engineering-governance-" + "a" * 40, True, False),
    ],
)
def test_pull_request_target_fallback_selects_only_exact_head_artifact(
    tmp_path, artifact_name, duplicate, expected_success
):
    artifacts = [{
        "id": 55,
        "name": artifact_name,
        "expired": False,
        "workflow_run": {"id": 123},
    }]
    if duplicate:
        artifacts.append({**artifacts[0], "id": 56})
    proc, output = _run_gate_artifact_resolution(
        tmp_path,
        event="pull_request_target",
        artifacts=artifacts,
        event_head="a" * 40,
    )

    assert (proc.returncode == 0) is expected_success
    if expected_success:
        assert "head_sha=" + "a" * 40 in output
        assert "artifact_id=55" in output
    else:
        assert "GOVERNANCE_RECEIPT_MISSING_OR_DUPLICATE" in proc.stderr


def test_stale_pr_head_aborts_early_without_accepting_existing_artifact(tmp_path):
    artifacts = [{
        "id": 55,
        "name": "wow-v17-engineering-governance-" + "a" * 40,
        "expired": False,
        "workflow_run": {"id": 123},
    }]
    proc, output = _run_gate_artifact_resolution(
        tmp_path, event="pull_request_target", artifacts=artifacts,
        event_head="a" * 40, live_head="b" * 40,
    )
    assert proc.returncode == 0, proc.stderr
    assert output.splitlines() == ["applicable=false"]
    assert "GOVERNANCE_STALE_HEAD_DEFERRED" in proc.stdout
    assert "artifact_id=" not in output
    assert "head_sha=" not in output


def test_release_verification_requires_trusted_exact_head_governance_and_exact_merge_sha():
    text = RELEASE.read_text(encoding="utf-8")
    assert "startsWith(github.event.pull_request.head.ref" not in text
    assert "Resolve exact merged identity" in text
    assert 'git merge-base --is-ancestor "$merge_sha" HEAD' in text
    assert "Verify required exact-head CI" in text
    assert '"wow-verify|.github/workflows/wow-verify.yml"' in text
    assert '"wow-engine-verify|.github/workflows/wow-engine-verify.yml"' in text
    assert "Trusted exact-head engineering governance" in text
    assert "/actions/runs?head_sha=${HEAD_SHA}&per_page=100" in text
    assert "Resolve trusted exact-head governance artifact" in text
    assert 'artifact_name="wow-v17-engineering-governance-${HEAD_SHA}"' in text
    assert "Verify governance-producing workflow provenance" in text
    assert 'protected_main_source=false' in text
    assert '[ "$head_repo" = "$GITHUB_REPOSITORY" ]' in text
    assert "wow-v17-existing-pr-governance-review.yml" in text
    assert "pull_request_target" in text
    assert "Download and verify trusted governance receipt" in text
    assert "RELEASE_GOVERNANCE_RECEIPT_CHECKSUM_MISMATCH" in text
    assert "verify_engineering_governance_receipt.py" in text
    assert "verify_existing_pr_governance_receipt.py" in text
    assert '--head-sha "$HEAD_SHA"' in text
    assert '--head-ref "$HEAD_REF"' in text
    assert '--workflow-run-id "$RUN_ID"' in text
    assert '--workflow-name "$WORKFLOW_NAME"' in text
    assert "trusted_handoff_receipt: VERIFIED" in text



def test_trusted_governance_stale_pr_artifact_waits_are_bounded_and_cancelled():
    """P1 regression: don't occupy 30min runners on obsolete exact-head SHAs.

    This does not grant QA/review acceptance, remove artifact hashes, or change
    the independent workflow_run path. Superseded PR heads are nonapplicable.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    text = (root / ".github/workflows/wow-v17-trusted-governance-gate.yml").read_text()
    assert "cancel-in-progress: true" in text
    assert "format('pr-{0}', github.event.pull_request.number)" in text
    assert "format('run-{0}', github.event.workflow_run.id)" in text
    assert "github.event.pull_request.head.sha) }}" not in text.split("concurrency:")[1].split("jobs:")[0]
    assert "for _ in $(seq 1 18); do" in text
    assert 'gh api "/repos/${GITHUB_REPOSITORY}/pulls/${pr_number}"' in text
    assert 'if [ "$current_head" != "$head_sha" ]; then' in text
    assert "GOVERNANCE_STALE_HEAD_DEFERRED" in text
    assert 'echo "applicable=false" >> "$GITHUB_OUTPUT"' in text
    assert "GOVERNANCE_LIVE_PR_LOOKUP_FAILED" in text
    assert "GOVERNANCE_RECEIPT_MISSING_OR_DUPLICATE" in text
    assert "GOVERNANCE_CERTIFIER_RECEIPT_TARGET_MISMATCH" in text
