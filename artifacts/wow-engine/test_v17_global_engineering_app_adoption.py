"""Global exact-source Engineering App adoption regression and denial cases."""
from __future__ import annotations

import importlib
from pathlib import Path
from unittest import mock

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
from v17 import engineering_authorized_adoption as adoption

OWNER = "gregoryharper84-ship-it"
SHA = "a" * 40
BASE = "b" * 40
REPO = "gregoryharper84-ship-it/WOW-Dashboard"
REQUIRED = adoption.REQUIRED_CHECKS


def source_pr(*, author=OWNER, sha=SHA, n=1, draft=False):
    return {
        "number": n, "state": "open", "draft": draft, "changed_files": 2,
        "user": {"login": author},
        "head": {"sha": sha, "repo": {"full_name": REPO}},
        "base": {"sha": BASE, "ref": "main", "repo": {"full_name": REPO}},
    }


def source_files():
    return [
        {"filename": ".github/workflows/wow-v17-engineering-provider-dispatcher.yml",
         "status": "modified"},
        {"filename": "artifacts/wow-engine/v17/engineering_gemini_headless.py",
         "status": "modified"},
    ]


def source_checks(*, conclusion="success", sha=SHA):
    checks = [{"name": name, "app": {"id": adoption.ACTIONS_APP_ID},
               "head_sha": sha, "id": i + 1,
               "status": "completed", "conclusion": conclusion}
              for i, name in enumerate(REQUIRED)]
    return {"total_count": len(checks), "check_runs": checks}


def test_global_protected_paths_accept_required_regression_only():
    base, paths = adoption.validate_evidence(
        source_pr(), source_files(), source_checks(), requested_pr=1, requested_sha=SHA)
    assert base == BASE
    assert paths == sorted(x["filename"] for x in source_files())
    assert adoption.branch_for(2058, SHA) == f"wow-engineering/exact-replay-2058-{SHA[:12]}"


@pytest.mark.parametrize("author,draft", [("a-foreign-user", False), (OWNER, True)])
def test_wrong_pr_actor_or_draft_held(author, draft):
    with pytest.raises(adoption.AdoptionHold, match="SOURCE_PR_OR_HEAD_PROVENANCE_HOLD"):
        adoption.validate_evidence(source_pr(author=author, draft=draft),
                                   source_files(), source_checks(),
                                   requested_pr=1, requested_sha=SHA)


@pytest.mark.parametrize("checks,reason", [
    (source_checks(conclusion="failure"), "SOURCE_REQUIRED_REGRESSION_RED_HOLD"),
    (source_checks(conclusion="queued"), "SOURCE_REQUIRED_REGRESSION_RED_HOLD"),
    (source_checks(sha="c" * 40), "SOURCE_REQUIRED_REGRESSION_MISSING_HOLD"),
    ({"check_runs": source_checks()["check_runs"], "total_count": 99},
     "SOURCE_CHECK_INVENTORY_INCOMPLETE_HOLD"),
])
def test_required_exact_head_regressions_cannot_be_faked(checks, reason):
    with pytest.raises(adoption.AdoptionHold, match=reason):
        adoption.validate_evidence(source_pr(), source_files(), checks,
                                   requested_pr=1, requested_sha=SHA)


@pytest.mark.parametrize("path,status,previous", [
    ("../escape.py", "modified", None),
    ("/etc/passwd", "modified", None),
    (".env.production", "modified", None),
    ("backend/new_model.py", "added", None),
    ("docs/file.md", "removed", None),
    (".github/CODEOWNERS", "renamed", ".github/OLD"),
    (".github/workflows/a.yml", "modified", ".github/renamed"),
])
def test_unsafe_source_paths_fail_closed(path, status, previous):
    with pytest.raises(adoption.AdoptionHold, match="SOURCE_CHANGE_SCOPE_OR_RENAME_HOLD"):
        adoption.source_paths([{"filename": path, "status": status,
                                "previous_filename": previous}])


def test_duplicate_and_oversize_inventory_held():
    with pytest.raises(adoption.AdoptionHold, match="DUPLICATE_SOURCE_PATH_HOLD"):
        adoption.source_paths(source_files() + source_files())
    with pytest.raises(adoption.AdoptionHold, match="SOURCE_FILE_COUNT_INVALID_HOLD"):
        adoption.source_paths(source_files() * 16)


def test_discovery_accepts_owner_source_and_ignores_incomplete_pr_list(monkeypatch):
    # List-of-pulls lacks changed_files. Full source PR must be read back.
    list_item = source_pr()
    del list_item["changed_files"]
    visited = []
    def fetch(path):
        visited.append(path)
        if path.startswith("pulls?state=open&base=main"):
            return [list_item]
        if path.startswith("pulls?state=all&head="):
            return []
        if path == "pulls/1/files?per_page=100":
            return source_files()
        if path == "pulls/1":
            return source_pr()
        if path == f"commits/{SHA}/check-runs?per_page=100":
            return source_checks()
        raise AssertionError(path)
    monkeypatch.setattr(adoption, "api", fetch)
    result = adoption.discover()
    assert result["status"] == "SOURCE_READY"
    assert result["source_sha"] == SHA and result["pr_number"] == 1
    assert result["can_execute"] is False
    assert "pulls/1" in visited


def test_durable_duplicate_pr_never_selected(monkeypatch):
    def fetch(path):
        if path.startswith("pulls?state=open&base=main"):
            return [source_pr()]
        if path.startswith("pulls?state=all&head="):
            return [{"number": 555, "user": {"login": adoption.APP_AUTHOR}}]
        raise AssertionError(f"duplicate must skip provider mutation: {path}")
    monkeypatch.setattr(adoption, "api", fetch)
    assert adoption.discover()["status"] == "NO_ELIGIBLE_SOURCE"


def test_global_review_wip_limit_prevents_pr_flood(monkeypatch):
    app_open = [
        {"user": {"login": adoption.APP_AUTHOR},
         "head": {"ref": "wow-engineering/exact-replay-%d-aaaa" % n}}
        for n in range(3)
    ]
    called = []
    def fetch(path):
        called.append(path)
        if path.startswith("pulls?state=open&base=main"):
            return app_open + [source_pr()]
        raise AssertionError("review backlog may not dispatch another App PR")
    monkeypatch.setattr(adoption, "api", fetch)
    receipt = adoption.discover()
    assert receipt == {"status": "NO_ELIGIBLE_SOURCE", "detail": "APP_REVIEW_WIP_CAP",
                       "can_execute": False}
    assert len(called) == 1


def test_app_token_and_read_only_identity_are_required(monkeypatch):
    monkeypatch.delenv("APP_TOKEN", raising=False)
    monkeypatch.setenv("GH_TOKEN", "readonly-mock")
    with pytest.raises(adoption.AdoptionHold, match="APP_TOKEN_AND_READ_TOKEN_REQUIRED_HOLD"):
        adoption.integrate(2058, SHA)
    monkeypatch.setenv("APP_TOKEN", "readonly-mock")
    with pytest.raises(adoption.AdoptionHold, match="READ_AND_WRITE_IDENTITY_NOT_SEPARATED_HOLD"):
        adoption.integrate(2058, SHA)


def test_unreviewed_source_never_selected(monkeypatch):
    def fetch(path):
        if path.startswith("pulls?state=open&base=main"):
            return [source_pr()]
        if path.startswith("pulls?state=all&head="):
            return []
        if path == "pulls/1/files?per_page=100":
            return source_files()
        if path == "pulls/1":
            return source_pr()
        if path == f"commits/{SHA}/check-runs?per_page=100":
            return source_checks(conclusion="failure")
        raise AssertionError(path)
    monkeypatch.setattr(adoption, "api", fetch)
    assert adoption.discover()["status"] == "NO_ELIGIBLE_SOURCE"


def created_app_pr():
    return {
        "number": 777, "state": "open", "draft": False,
        "user": {"login": adoption.APP_AUTHOR},
        "head": {"ref": adoption.branch_for(2058, SHA), "sha": SHA,
                 "repo": {"full_name": REPO}},
        "base": {"ref": "main", "sha": BASE, "repo": {"full_name": REPO}},
    }


def test_created_pr_requires_authenticated_app_identity_and_exact_target():
    pr = adoption.validate_created_pr(
        [created_app_pr()], branch=adoption.branch_for(2058, SHA),
        integrated_sha=SHA, main_sha=BASE)
    assert pr["number"] == 777


@pytest.mark.parametrize("field,value,reason", [
    ("user", {"login": OWNER}, "POST_CREATE_APP_PR_ACTOR_MISMATCH_HOLD"),
    ("head", {"ref": "wrong-branch", "sha": SHA,
              "repo": {"full_name": REPO}}, "POST_CREATE_APP_PR_BRANCH_MISMATCH_HOLD"),
    ("head", {"ref": adoption.branch_for(2058, SHA), "sha": "c" * 40,
              "repo": {"full_name": REPO}}, "POST_CREATE_APP_PR_HEAD_MISMATCH_HOLD"),
    ("head", {"ref": adoption.branch_for(2058, SHA), "sha": SHA,
              "repo": {"full_name": "attacker/WOW-Dashboard"}},
     "POST_CREATE_APP_PR_HEAD_REPOSITORY_MISMATCH_HOLD"),
    ("base", {"ref": "develop", "sha": BASE,
              "repo": {"full_name": REPO}}, "POST_CREATE_APP_PR_BASE_REF_MISMATCH_HOLD"),
    ("base", {"ref": "main", "sha": "c" * 40,
              "repo": {"full_name": REPO}}, "POST_CREATE_APP_PR_BASE_SHA_MISMATCH_HOLD"),
    ("base", {"ref": "main", "sha": BASE,
              "repo": {"full_name": "attacker/WOW-Dashboard"}},
     "POST_CREATE_APP_PR_BASE_REPOSITORY_MISMATCH_HOLD"),
    ("state", "closed", "POST_CREATE_APP_PR_NOT_OPEN_HOLD"),
    ("draft", True, "POST_CREATE_APP_PR_NOT_OPEN_HOLD"),
    ("number", 0, "POST_CREATE_APP_PR_NUMBER_INVALID_HOLD"),
])
def test_created_pr_metadata_mismatch_fails_closed(field, value, reason):
    pr = created_app_pr()
    pr[field] = value
    with pytest.raises(adoption.AdoptionHold, match=reason):
        adoption.validate_created_pr(
            [pr], branch=adoption.branch_for(2058, SHA),
            integrated_sha=SHA, main_sha=BASE)


@pytest.mark.parametrize("inventory", [[], [created_app_pr(), created_app_pr()], [None]])
def test_created_pr_inventory_must_be_unambiguous(inventory):
    reason = ("POST_CREATE_APP_PR_METADATA_INVALID_HOLD" if inventory == [None]
              else "POST_CREATE_APP_PR_INVENTORY_HOLD")
    with pytest.raises(adoption.AdoptionHold, match=reason):
        adoption.validate_created_pr(
            inventory, branch=adoption.branch_for(2058, SHA),
            integrated_sha=SHA, main_sha=BASE)


def test_bootstrap_receipt_names_indispensable_exact_head_owner_action():
    receipt = adoption.review_boundary_receipt(777, SHA)
    assert receipt == {
        "status": "PR_CREATED_PENDING_INDEPENDENT_REVIEW",
        "bootstrap_status": "OWNER_EXACT_HEAD_APPROVAL_REQUIRED",
        "source_review_status": "REQUIRED_PENDING",
        "source_review_workflow": "wow-v17-independent-qa-check",
        "source_review_head_sha": SHA,
        "independent_source_review_complete": False,
        "required_owner_login": OWNER,
        "required_owner_approval_head": SHA,
        "pr": 777,
        "sha": SHA,
        "can_execute": False,
    }


def test_controller_reuses_existing_schedule_and_app_not_owner_token():
    wf = yaml.safe_load((ROOT / ".github/workflows/wow-v17-engineering-auto-repair.yml").read_text())
    # YAML parsers disagree on the on key (some YAML 1.1 decode it as True).
    triggers = wf.get("on", wf.get(True))
    assert "schedule" in triggers
    assert "workflow_run" in triggers
    assert "workflow_dispatch" in triggers
    steps = wf["jobs"]["adopt-reviewed-owner-source"]["steps"]
    tokens = [step for step in steps if "Mint authenticated Engineering App token" == step.get("name")]
    assert len(tokens) == 1
    assert tokens[0]["uses"] == "./.github/actions/wow-engineering-token"
    assert wf["jobs"]["adopt-reviewed-owner-source"]["environment"] == "wow-engineering"
    assert "wow-v17-engineering-implementation-single-writer" in str(
        wf["jobs"]["adopt-reviewed-owner-source"]["concurrency"])
    checkout = steps[0]["with"]
    assert checkout["ref"] == "main"
    assert checkout["persist-credentials"] is False
    assert any("engineering_authorized_adoption.py integrate" in str(x.get("run", "")) for x in steps)
    text = "\n".join(str(s.get("run", "")) for s in steps)
    assert "APP_TOKEN" in str(steps) and "WOW_DRY_RUN_ONLY" in str(steps)
    assert "gh pr merge" not in text and "git push --force" not in text
    assert "independent_source_review.py" not in text
    assert "gh pr review" not in text.lower()
    assert "--approve" not in text.lower()
    integration = next(step["run"] for step in steps
                       if "engineering_authorized_adoption.py integrate" in step.get("run", ""))
    assert ".source_review_status == \"REQUIRED_PENDING\"" in integration
    assert ".independent_source_review_complete == false" in integration
    assert ".source_review_head_sha == .sha" in integration
    assert ".required_owner_approval_head == .sha" in integration


def test_protected_adoption_program_enforces_two_actual_principals():
    text = (ROOT / "artifacts/wow-engine/v17/engineering_authorized_adoption.py").read_text()
    assert 'os.environ.get("APP_TOKEN") == os.environ.get("GH_TOKEN")' in text
    assert "author.get(\"login\") == APP_AUTHOR" in text
    assert "validate_created_pr(" in text
    assert "git\", \"push\"" in text
    assert "gh\", \"pr\", \"create\"" in text
    assert "git\", \"restore\"" in text
    assert "41898282+github-actions[bot]@users.noreply.github.com" in text
    assert "wow-engineering[bot]@users.noreply.github.com" not in text
    assert "auto-merge" in text.lower()


def test_hourly_rotation_reaches_old_and_new_owner_prs_without_extra_writers(monkeypatch):
    """Regression: newest-first constant MAX_SCAN previously starved older PRs."""
    sources = [source_pr(n=n, sha=f"{n:040x}") for n in range(1, 61)]
    selected = []
    def fetch(path):
        if path == "pulls?state=open&base=main&per_page=100":
            return sources
        if path == "pulls?state=open&base=main&per_page=100&page=1":
            return sources
        if path.startswith("pulls?state=all&head="):
            # Mark every candidate except the two fixtures already replayed.
            n = int(path.split("exact-replay-")[1].split("-")[0])
            return [] if n in {1, 60} else [{"number": 999}]
        if path in {"pulls/1/files?per_page=100", "pulls/60/files?per_page=100"}:
            return source_files()
        if path == "pulls/1":
            return sources[0]
        if path == "pulls/60":
            return sources[-1]
        if path == f"commits/{sources[0]['head']['sha']}/check-runs?per_page=100":
            return source_checks(sha=sources[0]["head"]["sha"])
        if path == f"commits/{sources[-1]['head']['sha']}/check-runs?per_page=100":
            return source_checks(sha=sources[-1]["head"]["sha"])
        raise AssertionError(path)
    monkeypatch.setattr(adoption, "api", fetch)
    older = adoption.discover(slot=0)
    newer = adoption.discover(slot=1)
    assert (older["status"], older["pr_number"]) == ("SOURCE_READY", 1)
    assert (newer["status"], newer["pr_number"]) == ("SOURCE_READY", 60)
    assert older["can_execute"] is False and newer["can_execute"] is False


def test_review_wip_cap_accounts_for_pending_app_prs_beyond_first_page(monkeypatch):
    owner = source_pr(n=3)
    app = lambda n: {
        "number": n, "state": "open", "draft": False,
        "user": {"login": adoption.APP_AUTHOR},
        "head": {"ref": f"wow-engineering/exact-replay-{n}-aaaaaaaaaaaa",
                 "repo": {"full_name": REPO}},
    }
    first = [owner, app(10), app(11)] + [
        {"number": n, "state": "open", "draft": False,
         "user": {"login": "another-user"}, "head": {"repo": {"full_name": REPO}}}
        for n in range(97)
    ]
    assert len(first) == 100
    def fetch(path):
        if path in ("pulls?state=open&base=main&per_page=100",
                    "pulls?state=open&base=main&per_page=100&page=1"):
            return first
        if path == "pulls?state=open&base=main&per_page=100&page=2":
            return [app(12)]
        raise AssertionError("review WIP must fail closed before a source read: " + path)
    monkeypatch.setattr(adoption, "api", fetch)
    result = adoption.discover(slot=0)
    assert result["detail"] == "APP_REVIEW_WIP_CAP"
    assert result["can_execute"] is False


def test_inventory_page_limit_does_not_silently_ignore_older_prs(monkeypatch):
    full = [source_pr(n=n, sha=f"{n:040x}") for n in range(1, 101)]
    def fetch(path):
        if path.startswith("pulls?state=open&base=main&per_page=100"):
            return full
        raise AssertionError(path)
    monkeypatch.setattr(adoption, "api", fetch)
    result = adoption.discover(slot=0)
    assert result["detail"] == "SOURCE_PR_INVENTORY_PAGE_CAP"
    assert result["can_execute"] is False


@pytest.mark.parametrize("slot", [-1, "not-an-hour", False])
def test_invalid_scan_slot_is_held(slot, monkeypatch):
    monkeypatch.setattr(adoption, "api", lambda path: [])
    with pytest.raises(adoption.AdoptionHold, match="SCAN_SLOT_INVALID_HOLD"):
        adoption.discover(slot=slot)


def test_more_than_100_real_check_runs_are_admitted_without_evidence_loss(monkeypatch):
    """Exact-head pagination does not permanently starve busy reviewed PRs."""
    rows = [
        {"id": i + 1, "name": "irrelevant check", "app": {"id": adoption.ACTIONS_APP_ID},
         "status": "completed", "conclusion": "success", "head_sha": SHA}
        for i in range(147)
    ] + [
        {"id": 148 + i, "name": name, "app": {"id": adoption.ACTIONS_APP_ID},
         "status": "completed", "conclusion": "success", "head_sha": SHA}
        for i, name in enumerate(REQUIRED)
    ]
    assert len(rows) == 150
    seen = []
    def fetch(path):
        seen.append(path)
        if path == f"commits/{SHA}/check-runs?per_page=100":
            return {"total_count": 150, "check_runs": rows[:100]}
        if path == f"commits/{SHA}/check-runs?per_page=100&page=2":
            return {"total_count": 150, "check_runs": rows[100:]}
        raise AssertionError(path)
    monkeypatch.setattr(adoption, "api", fetch)
    checks = adoption._exact_head_checks(SHA)
    assert checks["total_count"] == 150
    assert len(checks["check_runs"]) == 150
    assert len(seen) == 2
    base, paths = adoption.validate_evidence(
        source_pr(), source_files(), checks,
        requested_pr=1, requested_sha=SHA,
    )
    assert base == BASE and len(paths) == 2


@pytest.mark.parametrize("bad_second,reason", [
    ({"total_count": 151, "check_runs": [{"id": 200}]},
     "SOURCE_CHECK_INVENTORY_INCOMPLETE_HOLD"),
    ({"total_count": 150, "check_runs": []},
     "SOURCE_CHECK_INVENTORY_INCOMPLETE_HOLD"),
    ({"total_count": 150, "check_runs": [{"id": i + 1} for i in range(50)]},
     "SOURCE_CHECK_INVENTORY_DUPLICATE_OR_INVALID_HOLD"),
])
def test_check_run_page_mutation_or_duplication_fails_closed(monkeypatch, bad_second, reason):
    first = {"total_count": 150, "check_runs": [{"id": i + 1} for i in range(100)]}
    def fetch(path):
        if path.endswith("per_page=100"):
            return first
        if path.endswith("page=2"):
            return bad_second
        raise AssertionError(path)
    monkeypatch.setattr(adoption, "api", fetch)
    with pytest.raises(adoption.AdoptionHold, match=reason):
        adoption._exact_head_checks(SHA)


def test_overlarge_check_run_inventory_is_typed_hold(monkeypatch):
    monkeypatch.setattr(
        adoption, "api",
        lambda path: {"total_count": adoption.MAX_CHECK_PAGES * 100 + 1, "check_runs": []},
    )
    with pytest.raises(adoption.AdoptionHold, match="SOURCE_CHECK_INVENTORY_PAGE_CAP_HOLD"):
        adoption._exact_head_checks(SHA)
