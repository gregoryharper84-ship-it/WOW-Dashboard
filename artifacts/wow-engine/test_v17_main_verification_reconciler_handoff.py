"""Execute the real protected-main reconciliation step with a deterministic gh stub.

This regression suite is read-only and never contacts GitHub or Render.
It rejects the historical false-green: verified main with no downstream
release-controller workflow run.
"""
import json
import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "wow-v17-main-verification-reconciler.yml"
MAIN = "a" * 40


def _script():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    return textwrap.dedent(
        workflow.split(
            "      - name: Reconcile exact-main verification workflow\n"
            "        shell: bash\n"
            "        run: |\n",
            1,
        )[1]
    )


def _run(tmp_path, *, verify=None, release=None, fail_release_api=False):
    if shutil.which("jq") is None:
        pytest.skip("GitHub hosted Ubuntu runners supply jq")
    gh = tmp_path / "gh"
    gh.write_text(
        '#!/usr/bin/env python3\n'
        'import json, os, sys\n'
        'case = json.loads(os.environ["STUB_GH_CASE"])\n'
        'endpoint = next((arg for arg in sys.argv if arg.startswith("repos/")), "")\n'
        'if "/branches/main" in endpoint:\n'
        '    print(case["main"])\n'
        'elif "/actions/workflows/wow-verify.yml/runs?" in endpoint:\n'
        '    print(json.dumps(case["verify"]))\n'
        'elif "/actions/workflows/wow-v17-render-production-deploy.yml/runs?" in endpoint:\n'
        '    if case.get("fail_release_api"):\n'
        '        sys.exit(1)\n'
        '    print(json.dumps(case["release"]))\n'
        'elif "/actions/workflows/wow-verify.yml/dispatches" in endpoint:\n'
        '    print("DISPATCH_MOCK_ONLY")\n'
        'else:\n'
        '    sys.exit(3)\n',
        encoding="utf-8",
    )
    gh.chmod(0o755)
    env = os.environ.copy()
    env.update({
        "PATH": str(tmp_path) + os.pathsep + env.get("PATH", ""),
        "GITHUB_REPOSITORY": "gregoryharper84-ship-it/WOW-Dashboard",
        "STUB_GH_CASE": json.dumps({
            "main": MAIN, "verify": verify if verify is not None else [],
            "release": release if release is not None else [],
            "fail_release_api": fail_release_api,
        }),
        "WOW_CAN_EXECUTE": "false",
        "WOW_DRY_RUN_ONLY": "true",
    })
    return subprocess.run(
        ["bash", "-e"], input=_script(), text=True,
        capture_output=True, env=env, timeout=8, check=False,
    )


def _run_item(*, status="completed", conclusion="success", sha=MAIN, event="workflow_dispatch",
              source_sha=None, source_id=7001, run_id=7001):
    # GitHub release workflow run-name is derived from its authenticated
    # triggering verification event, which can differ from downstream head_sha.
    label = ("release-" + (source_sha or sha) + "-verify-" + str(source_id)
             if event == "workflow_run" else "verify")
    return {
        "id": run_id, "head_sha": sha, "event": event, "status": status,
        "display_title": label,
        "conclusion": conclusion, "created_at": "2026-10-14T23:37:00Z",
    }


def test_green_verifier_without_release_is_error_not_false_green(tmp_path):
    result = _run(tmp_path, verify=[_run_item()])
    assert result.returncode == 1
    assert "EXACT_MAIN_RELEASE_HANDOFF_MISSING:" + MAIN in result.stdout
    assert "DISPATCH_MOCK_ONLY" not in result.stdout


def test_downstream_release_failure_is_reported_as_error(tmp_path):
    result = _run(tmp_path, verify=[_run_item()], release=[_run_item(conclusion="failure", event="workflow_run")])
    assert result.returncode == 1
    assert "EXACT_MAIN_RELEASE_HANDOFF_FAILED:completed|failure" in result.stdout


def test_downstream_release_pending_is_not_reported_deployed(tmp_path):
    result = _run(tmp_path, verify=[_run_item()], release=[_run_item(status="in_progress", conclusion=None, event="workflow_run")])
    assert result.returncode == 0
    assert "EXACT_MAIN_RELEASE_HANDOFF_PENDING:" in result.stdout
    assert "EXACT_SHA_RENDER_DEPLOY_LIVE" not in result.stdout


def test_downstream_release_success_has_explicit_handoff_receipt(tmp_path):
    result = _run(tmp_path, verify=[_run_item()], release=[_run_item(event="workflow_run")])
    assert result.returncode == 0
    assert "EXACT_MAIN_RELEASE_HANDOFF_COMPLETED:success" in result.stdout


def test_stale_sha_release_run_does_not_authorize_current_main(tmp_path):
    result = _run(tmp_path, verify=[_run_item()], release=[_run_item(sha="b" * 40, event="workflow_run")])
    assert result.returncode == 1
    assert "EXACT_MAIN_RELEASE_HANDOFF_MISSING:" in result.stdout


def test_newer_downstream_revision_still_matches_true_upstream_verified_sha(tmp_path):
    # Workflow_run release can execute a newer workflow revision B, while the
    # authoritative event identifies previously verified main revision A.
    release = _run_item(sha="b" * 40, source_sha=MAIN, event="workflow_run")
    result = _run(tmp_path, verify=[_run_item()], release=[release])
    assert result.returncode == 0
    assert "EXACT_MAIN_RELEASE_HANDOFF_COMPLETED:success" in result.stdout


def test_same_sha_from_different_verifier_run_is_not_accepted(tmp_path):
    # Comparing only downstream head_sha or only SHA would accept this.
    release = _run_item(sha=MAIN, source_id=9999, event="workflow_run")
    result = _run(tmp_path, verify=[_run_item(source_id=7001)], release=[release])
    assert result.returncode == 1
    assert "EXACT_MAIN_RELEASE_HANDOFF_MISSING:" in result.stdout


def test_missing_verifier_run_identity_fails_closed(tmp_path):
    verify = _run_item()
    verify.pop("id")
    result = _run(tmp_path, verify=[verify])
    assert result.returncode == 1
    assert "EXACT_MAIN_VERIFY_RUN_ID_UNVERIFIED" in result.stdout


def test_trusted_release_run_name_exposes_source_run_and_sha():
    release = WORKFLOW.with_name("wow-v17-render-production-deploy.yml").read_text()
    assert 'run-name: "release-${{ github.event.workflow_run.head_sha }}-verify-${{ github.event.workflow_run.id }}"' in release
    script = _script()
    assert '.display_title == $label' in script
    assert 'origin_label="release-${main_sha}-verify-${verify_run_id}"' in script
    assert '.head_sha == $sha and (.event == "workflow_run"' not in script


def test_downstream_api_unavailable_fails_closed(tmp_path):
    result = _run(tmp_path, verify=[_run_item()], fail_release_api=True)
    assert result.returncode == 1
    assert "EXACT_MAIN_RELEASE_HANDOFF_EVIDENCE_UNAVAILABLE" in result.stdout


def test_missing_verifier_dispatches_only_verification(tmp_path):
    result = _run(tmp_path)
    assert result.returncode == 0
    assert "MISSING_VERIFY_DISPATCHED_FOR_MAIN:" + MAIN in result.stdout
    assert "CHECKS_AND_INDEPENDENT_RELEASE_PROVENANCE_STILL_REQUIRED" in result.stdout
    assert "EXACT_MAIN_RELEASE_HANDOFF_COMPLETED" not in result.stdout


def test_reconciler_never_initiates_provider_deploy():
    source = _script()
    assert "api.render.com" not in source
    assert "RENDER_API_KEY" not in source
    assert "render deploys create" not in source
    assert "/actions/workflows/wow-verify.yml/dispatches" in source
