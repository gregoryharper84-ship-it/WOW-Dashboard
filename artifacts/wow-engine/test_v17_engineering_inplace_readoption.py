"""P0 #2136: fail-closed, no-fourth-PR exact-source App readoption."""
from __future__ import annotations

import copy
from pathlib import Path
import re
import subprocess

import pytest
import yaml

from v17 import engineering_authorized_adoption as adoption
from v17 import engineering_inplace_readoption as readopt

REPO = adoption.REPO
SOURCE_SHA = "a" * 40
OLDER_SOURCE_SHA = "b" * 40
APP_HEAD = "c" * 40
MAIN = "d" * 40
SOURCE_PR = 2198
APP_PR = 2208
BRANCH = f"wow-engineering/exact-replay-{SOURCE_PR}-{OLDER_SOURCE_SHA[:12]}"
PATHS = [
    ".github/workflows/wow-v17-remote-writer-finalize.yml",
    "artifacts/wow-engine/v17/engineering_remote_writer_release.py",
]


def app_pr():
    return {
        "number": APP_PR, "state": "open", "draft": False,
        "user": {"login": adoption.APP_AUTHOR, "id": 339932922},
        "head": {"ref": BRANCH, "sha": APP_HEAD,
                 "repo": {"full_name": REPO}},
        "base": {"ref": "main", "sha": MAIN,
                 "repo": {"full_name": REPO}},
        "body": (
            "Incident: #2020\n\n"
            f"Original author/source: PR #{SOURCE_PR}, head \x60{OLDER_SOURCE_SHA}\x60.\n"
            f"Integration head: \x60{APP_HEAD}\x60.\n"
            "No auto-merge. can_execute=false.\n"
        ),
    }


def _network(monkeypatch, *, pr=None, delta=None):
    row = copy.deepcopy(pr if pr is not None else app_pr())
    changed = delta if delta is not None else [
        {"filename": PATHS[1], "status": "modified"}]
    calls = []
    def fetch(endpoint):
        calls.append(endpoint)
        if endpoint == f"pulls/{APP_PR}":
            return copy.deepcopy(row)
        if endpoint == f"pulls/{APP_PR}/files?per_page=100":
            return [{"filename": p} for p in PATHS]
        if endpoint == f"compare/{OLDER_SOURCE_SHA}...{SOURCE_SHA}?per_page=100":
            return {
                "status": "ahead", "behind_by": 0,
                "merge_base_commit": {"sha": OLDER_SOURCE_SHA},
                "files": changed,
            }
        raise AssertionError("unexpected authenticated read: " + endpoint)
    monkeypatch.setattr(adoption, "api", fetch)
    monkeypatch.setattr(adoption, "preflight",
                        lambda number, sha: (MAIN, PATHS, "e" * 40))
    return calls


def test_targeted_readoption_at_full_wip_does_not_discover_new_branch(monkeypatch):
    calls = _network(monkeypatch)
    main, paths, pr, previous, branch = readopt._prepare(
        SOURCE_PR, SOURCE_SHA, APP_PR)
    assert main == MAIN
    assert paths == PATHS and previous == OLDER_SOURCE_SHA
    assert branch == BRANCH
    assert pr["head"]["sha"] == APP_HEAD
    assert all("pulls?state=open" not in path for path in calls)
    assert all("pulls?state=all&head" not in path for path in calls)
    assert not any("create" in path for path in calls)


@pytest.mark.parametrize("field,value,code", [
    ("state", "closed", "READOPTION_APP_PR_IDENTITY_HOLD"),
    ("draft", True, "READOPTION_APP_PR_IDENTITY_HOLD"),
    ("user", {"login": adoption.OWNER_LOGIN, "id": 123}, "READOPTION_APP_PR_IDENTITY_HOLD"),
    ("user", {"login": adoption.APP_AUTHOR, "id": 123}, "READOPTION_APP_PR_IDENTITY_HOLD"),
    ("number", 12345, "READOPTION_APP_PR_IDENTITY_HOLD"),
    ("head", {"ref": BRANCH, "sha": APP_HEAD,
              "repo": {"full_name": "attacker/fork"}}, "READOPTION_APP_PR_IDENTITY_HOLD"),
    ("base", {"ref": "unsafe", "sha": MAIN,
              "repo": {"full_name": REPO}}, "READOPTION_APP_PR_IDENTITY_HOLD"),
    ("body", "Incident: #2020\nunrelated prose", "READOPTION_ORIGINAL_SOURCE_UNPROVEN_HOLD"),
])
def test_untrusted_existing_app_target_never_admitted(monkeypatch, field, value, code):
    pr = app_pr()
    pr[field] = value
    _network(monkeypatch, pr=pr)
    with pytest.raises(adoption.AdoptionHold, match=code):
        readopt._prepare(SOURCE_PR, SOURCE_SHA, APP_PR)


@pytest.mark.parametrize("delta", [
    [],
    [{"filename": "attacker/file.py", "status": "modified"}],
    [{"filename": PATHS[1], "status": "removed"}],
    [{"filename": PATHS[1], "status": "renamed"}],
])
def test_no_delta_or_foreign_path_is_denied(monkeypatch, delta):
    _network(monkeypatch, delta=delta)
    with pytest.raises(adoption.AdoptionHold, match="READOPTION_SOURCE_DELTA_INVALID_HOLD"):
        readopt._prepare(SOURCE_PR, SOURCE_SHA, APP_PR)


def test_exact_app_bytes_must_match_previous_certified_source(monkeypatch):
    paths = PATHS
    def fake_call(*cmd):
        assert cmd[:2] == ("git", "rev-parse")
        spec = cmd[2]
        if spec.startswith(OLDER_SOURCE_SHA + ":"):
            return "f" * 40
        if spec.startswith(APP_HEAD + ":"):
            return "f" * 40 if spec.endswith(paths[0]) else "0" * 40
        raise AssertionError(spec)
    monkeypatch.setattr(adoption, "call", fake_call)
    with pytest.raises(adoption.AdoptionHold, match="READOPTION_APP_BYTES_DIVERGED_HOLD"):
        readopt._verify_app_bytes(APP_HEAD, OLDER_SOURCE_SHA, paths)


@pytest.mark.parametrize("env", [
    {},
    {"APP_TOKEN": "same", "GH_TOKEN": "same", "WOW_CAN_EXECUTE": "false",
     "WOW_DRY_RUN_ONLY": "true"},
    {"APP_TOKEN": "app", "GH_TOKEN": "read", "WOW_CAN_EXECUTE": "true",
     "WOW_DRY_RUN_ONLY": "true"},
])
def test_no_untrusted_writer_token_or_execution_mode(monkeypatch, env):
    for key in ("APP_TOKEN", "GH_TOKEN", "WOW_CAN_EXECUTE", "WOW_DRY_RUN_ONLY"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(readopt, "preflight",
                        lambda *args: pytest.fail("untrusted writer entered preflight"))
    with pytest.raises(adoption.AdoptionHold):
        readopt.readopt(SOURCE_PR, SOURCE_SHA, APP_PR)


def test_workflow_manual_target_in_single_existing_app_writer():
    root = Path(__file__).resolve().parents[2]
    path = root / ".github/workflows/wow-v17-engineering-auto-repair.yml"
    wf = yaml.safe_load(path.read_text())
    triggers = wf.get("on", wf.get(True))
    assert set(triggers["workflow_dispatch"]["inputs"]) == {
        "source_pr", "source_sha", "readopt_app_pr"}
    assert wf["jobs"]["adopt-reviewed-owner-source"]["environment"] == "wow-engineering"
    assert "wow-v17-engineering-implementation-single-writer" in str(
        wf["jobs"]["adopt-reviewed-owner-source"]["concurrency"])
    assert wf["permissions"]["contents"] == "read"
    assert wf["permissions"]["pull-requests"] == "read"
    script = next(step["run"] for step in
                  wf["jobs"]["adopt-reviewed-owner-source"]["steps"]
                  if "engineering_inplace_readoption.py readopt" in str(step.get("run", "")))
    assert script.count("engineering_inplace_readoption.py readopt") == 1
    assert script.count("engineering_authorized_adoption.py integrate") == 1
    assert "if [ -n \"$TARGET_READOPT_APP_PR\" ]; then" in script
    assert "PR_UPDATED_PENDING_INDEPENDENT_REVIEW" in script
    assert "PR_CREATED_PENDING_INDEPENDENT_REVIEW" in script
    assert "--force" not in script
    assert "gh pr merge" not in script
    assert subprocess.run(["bash", "-n"], input=script, text=True,
                          capture_output=True).returncode == 0


def test_readoption_contract_has_no_extra_controller_or_release_authority():
    root = Path(__file__).resolve().parents[2]
    source = (root / "artifacts/wow-engine/v17/engineering_inplace_readoption.py").read_text()
    assert 'adoption.call("git", "push", "origin"' in source
    assert "APP_TOKEN" in source and "GH_TOKEN" in source
    assert "READOPTION_APP_HEAD_MOVED_BEFORE_PUSH_HOLD" in source
    assert "READOPTION_APP_BYTES_DIVERGED_HOLD" in source
    assert "PR_UPDATED_PENDING_INDEPENDENT_REVIEW" in source
    assert "git push --force" not in source
    assert not re.search(r"\b(merge_pull_request|approve|trigger_deploy)\(", source)


def test_valid_readoption_pushes_only_existing_app_branch_and_requires_new_reviews(monkeypatch):
    """Simulate the actual readoption transition with zero live network or git."""
    monkeypatch.setenv("APP_TOKEN", "separate-app-principal")
    monkeypatch.setenv("GH_TOKEN", "separate-readonly-principal")
    monkeypatch.setenv("WOW_CAN_EXECUTE", "false")
    monkeypatch.setenv("WOW_DRY_RUN_ONLY", "true")
    new_head = "e" * 40
    pr = app_pr()
    receipt = {
        "status": "READOPTION_ADMISSION_PASS", "pr": APP_PR,
        "existing_app_head": APP_HEAD, "main": MAIN,
    }
    monkeypatch.setattr(readopt, "preflight", lambda *args: receipt)
    monkeypatch.setattr(readopt, "_prepare", lambda *args:
                        (MAIN, PATHS, copy.deepcopy(pr), OLDER_SOURCE_SHA, BRANCH))
    events = []
    state = {"pushed": False, "body": pr["body"]}

    def mocked_call(*cmd, env=None):
        events.append(cmd)
        if cmd[:3] == ("git", "diff", "--cached"):
            return "\n".join(PATHS) if "--name-only" in cmd else ""
        if cmd[:2] == ("git", "rev-parse"):
            if cmd[2] == "HEAD":
                return new_head
            if cmd[2].startswith(":"):
                return "5" * 40
            if cmd[2].startswith(SOURCE_SHA + ":"):
                return "5" * 40
            raise AssertionError(cmd)
        if cmd[:3] == ("git", "push", "origin"):
            assert not any("force" in item for item in cmd)
            assert env is not None and env["GIT_TERMINAL_PROMPT"] == "0"
            state["pushed"] = True
            return ""
        if cmd[:3] == ("gh", "api", "--method"):
            assert env["GH_TOKEN"] == "separate-app-principal"
            assert cmd[3] == "PATCH"
            state["body"] = next(x.split("=", 1)[1]
                                 for x in cmd if x.startswith("body="))
            return "{}"
        return ""

    def mocked_api(route):
        if route == "git/ref/heads/main":
            return {"object": {"sha": MAIN}}
        if route == f"pulls/{SOURCE_PR}":
            return {"head": {"sha": SOURCE_SHA}}
        if route == f"pulls/{APP_PR}":
            result = copy.deepcopy(pr)
            if state["pushed"]:
                result["head"]["sha"] = new_head
            result["body"] = state["body"]
            return result
        raise AssertionError(route)

    monkeypatch.setattr(adoption, "call", mocked_call)
    monkeypatch.setattr(adoption, "api", mocked_api)
    result = readopt.readopt(SOURCE_PR, SOURCE_SHA, APP_PR)
    assert result["status"] == "PR_UPDATED_PENDING_INDEPENDENT_REVIEW"
    assert result["pr"] == APP_PR
    assert result["source_review_status"] == "REQUIRED_PENDING"
    assert result["independent_source_review_complete"] is False
    assert result["required_owner_approval_head"] == new_head
    assert result["can_execute"] is False
    assert sum(event[:3] == ("git", "push", "origin") for event in events) == 1
    assert not any(event[:3] == ("gh", "pr", "create") for event in events)
    assert "Original author/source: PR #2198" in state["body"]
