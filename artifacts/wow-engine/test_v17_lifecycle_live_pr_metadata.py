"""Regress #2136: live PR metadata, immutable head and no stale-event approval."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "artifacts/wow-engine/v17/engineering_lifecycle_live_pr.py"
WORKFLOW = ROOT / ".github/workflows/wow-v17-engineering-auditor-code-health.yml"
REPO = "gregoryharper84-ship-it/WOW-Dashboard"
HEAD = "a" * 40
PR = 2199


def _module():
    spec = importlib.util.spec_from_file_location("engineering_lifecycle_live_pr", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _pr(body="Lifecycle-Control-Plane: true\nChange-Class: B"):
    return {
        "number": PR, "state": "open", "draft": False, "body": body,
        "head": {"sha": HEAD, "repo": {"full_name": REPO}},
        "base": {"ref": "main", "repo": {"full_name": REPO}},
    }


def _validate(module, payload):
    return module.validated_snapshot(
        payload, repository=REPO, number=PR, head_sha=HEAD,
    )


def test_live_body_supersedes_frozen_event_without_changing_source_head():
    mod = _module()
    stale_event_body = "Lifecycle-Control-Plane: true"
    live = _pr()
    assert live["body"] != stale_event_body
    result = _validate(mod, live)
    assert result["body"] == live["body"]
    assert result["head_sha"] == HEAD
    mod.verify_unchanged(result, _validate(mod, copy.deepcopy(live)))


@pytest.mark.parametrize("mutation,code", [
    (lambda x: x.update(number=PR + 1), "LIFECYCLE_LIVE_PR_IDENTITY_MISMATCH"),
    (lambda x: x["head"].update(sha="b" * 40), "LIFECYCLE_LIVE_PR_IDENTITY_MISMATCH"),
    (lambda x: x["head"]["repo"].update(full_name="forged/repo"), "LIFECYCLE_LIVE_PR_IDENTITY_MISMATCH"),
    (lambda x: x["base"]["repo"].update(full_name="forged/repo"), "LIFECYCLE_LIVE_PR_IDENTITY_MISMATCH"),
    (lambda x: x["base"].update(ref="other"), "LIFECYCLE_LIVE_PR_IDENTITY_MISMATCH"),
    (lambda x: x.update(state="closed"), "LIFECYCLE_LIVE_PR_NOT_REVIEWABLE"),
    (lambda x: x.update(draft=True), "LIFECYCLE_LIVE_PR_NOT_REVIEWABLE"),
    (lambda x: x.update(body=17), "LIFECYCLE_LIVE_PR_BODY_INVALID"),
    (lambda x: x["head"].update(repo=None), "LIFECYCLE_LIVE_PR_REPOSITORY_UNVERIFIED"),
])
def test_untrusted_or_stale_pr_metadata_fails_closed(mutation, code):
    mod = _module()
    payload = _pr()
    mutation(payload)
    with pytest.raises(mod.LivePRMetadataHold, match=code):
        _validate(mod, payload)


def test_metadata_change_during_audit_fails_even_at_same_sha():
    mod = _module()
    first = _validate(mod, _pr("Change-Class: A"))
    later = _validate(mod, _pr("Change-Class: C"))
    with pytest.raises(mod.LivePRMetadataHold, match="LIFECYCLE_LIVE_PR_CHANGED_DURING_AUDIT"):
        mod.verify_unchanged(first, later)


def test_cli_exports_live_body_then_rechecks_without_mutating_pr(tmp_path):
    before = tmp_path / "before.json"
    after = tmp_path / "after.json"
    body_path = tmp_path / "body.md"
    body = "Lifecycle-Control-Plane: true\nChange-Class: B\n"
    before.write_text(json.dumps(_pr(body)), encoding="utf-8")
    after.write_text(json.dumps(_pr(body)), encoding="utf-8")
    common = [
        sys.executable, str(MODULE_PATH), "--pr-json", str(before),
        "--repository", REPO, "--number", str(PR), "--head-sha", HEAD,
    ]
    first = subprocess.run(common + ["--body-output", str(body_path)],
                           capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    assert body_path.read_text(encoding="utf-8") == body
    good = subprocess.run(common + ["--recheck-json", str(after)],
                          capture_output=True, text=True)
    assert good.returncode == 0, good.stderr
    after.write_text(json.dumps(_pr("Change-Class: C")), encoding="utf-8")
    bad = subprocess.run(common + ["--recheck-json", str(after)],
                         capture_output=True, text=True)
    assert bad.returncode != 0
    assert "LIFECYCLE_LIVE_PR_CHANGED_DURING_AUDIT" in bad.stderr


def test_workflow_uses_live_github_pr_and_explicit_metadata_edit_wake():
    yaml = WORKFLOW.read_text(encoding="utf-8")
    assert "pull-requests: read" in yaml
    assert "edited, ready_for_review" in yaml
    assert "github.event.pull_request.body" not in yaml
    assert 'gh api "repos/${GITHUB_REPOSITORY}/pulls/${PR_NUMBER}"' in yaml
    assert "--head-sha" in yaml
    assert "--recheck-json" in yaml
    assert "engineering_lifecycle_live_pr.py" in yaml
    assert "can_execute: false" in yaml
