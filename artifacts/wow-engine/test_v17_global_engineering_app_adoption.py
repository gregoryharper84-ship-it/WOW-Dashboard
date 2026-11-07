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
        "number": n, "state": "open", "draft": draft, "mergeable": True, "changed_files": 2,
        "body": "Incident: #2136",
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



def main_compare(source_sha=SHA, main_sha=BASE, changes=(), status="diverged"):
    return {
        "status": status, "base_commit": {"sha": source_sha},
        "merge_base_commit": {"sha": main_sha},
        "files": [{"filename": path, "status": "modified"} for path in changes],
    }

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
    # This case exercises candidate identity/rotation, not local git IO.
    monkeypatch.setattr(adoption, "verify_git_source", lambda *args: BASE)
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
        if path == f"compare/{SHA}...{BASE}?per_page=100":
            return main_compare()
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
    observer = wf["jobs"]["adoption-readonly-preflight"]
    assert "concurrency" not in observer
    assert "environment" not in observer
    assert "engineering_authorized_adoption.py discover" in str(observer["steps"])
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
    # This case exercises candidate identity/rotation, not local git IO.
    monkeypatch.setattr(adoption, "verify_git_source", lambda *args: BASE)
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
        for selected in (sources[0], sources[-1]):
            identity = selected["head"]["sha"]
            if path == f"compare/{identity}...{BASE}?per_page=100":
                return main_compare(source_sha=identity)
        raise AssertionError(path)
    monkeypatch.setattr(adoption, "api", fetch)
    older = adoption.discover(slot=0)
    newer = adoption.discover(slot=1)
    assert (older["status"], older["pr_number"]) == ("SOURCE_READY", 1)
    assert (newer["status"], newer["pr_number"]) == ("SOURCE_READY", 60)
    assert older["can_execute"] is False and newer["can_execute"] is False



def test_priority_adoption_scans_p0_without_starving_routine_sources():
    sources = [source_pr(n=i, sha=f"{i:040x}") for i in range(1, 61)]
    sources[-1]["labels"] = [{"name": "P0"}]
    first, _ = adoption._adoption_candidate_window(sources, slot=0)
    urgent, _ = adoption._adoption_candidate_window(sources, slot=1)
    fair_next, _ = adoption._adoption_candidate_window(sources, slot=3)
    assert first[0]["number"] == 1
    assert urgent[0]["number"] == 60
    assert len(urgent) <= adoption.MAX_SCAN
    assert len({x["number"] for x in urgent}) == len(urgent)
    assert {x["number"] for x in fair_next} == set(range(51, 61))


def test_fair_rotation_covers_window_count_divisible_by_three():
    sources = [source_pr(n=i, sha=f"{i:040x}") for i in range(1, 151)]
    for row in sources[-10:]:
        row["labels"] = [{"name": "P0"}]
    fair = {
        adoption._adoption_candidate_window(sources, slot=slot)[0][0]["number"]
        for slot in (0, 3, 6)
    }
    assert fair == {1, 51, 101}


def test_urgent_metadata_cannot_grant_untrusted_authorship():
    sources = [source_pr(n=1)]
    sources[0]["labels"] = [{"name": "P0"}]
    sources[0]["user"]["login"] = "another-user"
    window, _ = adoption._adoption_candidate_window(sources, slot=1)
    assert window == []
    with pytest.raises(adoption.AdoptionHold, match="SOURCE_PR_OR_HEAD_PROVENANCE_HOLD"):
        adoption.validate_evidence(sources[0], source_files(), source_checks(),
                                   requested_pr=1, requested_sha=SHA)

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


def test_ci_completion_wakes_existing_single_writer_adoption():
    wf = yaml.safe_load((ROOT / ".github/workflows/wow-v17-engineering-auto-repair.yml").read_text())
    triggers = wf.get("on", wf.get(True))
    assert {"wow-verify", "wow-engine-verify"} <= set(triggers["workflow_run"]["workflows"])
    hold = wf["jobs"]["discover"]["if"]
    assert "wow-v17-independent-qa-check" in hold
    assert "wow-v17-release-authority-check" in hold
    assert "wow-verify" not in hold
    observer = wf["jobs"]["adoption-readonly-preflight"]
    expr = observer["if"]
    adopter = wf["jobs"]["adopt-reviewed-owner-source"]
    assert adopter["needs"] == "adoption-readonly-preflight"
    assert adopter["if"] == "needs.adoption-readonly-preflight.outputs.status == 'SOURCE_READY'"
    assert "concurrency" not in observer
    assert "github.event.workflow_run.conclusion == 'success'" in expr
    assert "github.event.workflow_run.event == 'pull_request'" in expr
    assert "github.event.workflow_run.head_repository.full_name == github.repository" in expr
    assert "github.event.workflow_run.name == 'wow-verify'" in expr
    assert "github.event.workflow_run.name == 'wow-engine-verify'" in expr
    assert "wow-v17-engineering-implementation-single-writer" in str(adopter["concurrency"])
    # Assert parsed workflow semantics, not Python's repr of YAML dicts:
    # str(steps) quotes keys/values and can false-fail correctly wired env.
    sources = [
        step for step in adopter["steps"]
        if step.get("name") == "Re-propose immutable source through authenticated Engineering App"
    ]
    assert len(sources) == 1
    source = sources[0]
    assert source["env"]["SOURCE_PR"] == "${{ needs.adoption-readonly-preflight.outputs.pr_number }}"
    assert source["env"]["SOURCE_SHA"] == "${{ needs.adoption-readonly-preflight.outputs.source_sha }}"
    assert source["env"]["WOW_CAN_EXECUTE"] == "false"
    assert source["env"]["WOW_DRY_RUN_ONLY"] == "true"
    assert '--source-pr "$SOURCE_PR" --source-sha "$SOURCE_SHA"' in source["run"]
    assert source["env"]["APP_TOKEN"] == "${{ steps.engineering_app.outputs.token }}"


def test_discovery_skips_conflicted_source_and_admits_next_verified_candidate(monkeypatch):
    # This case exercises candidate identity/rotation, not local git IO.
    monkeypatch.setattr(adoption, "verify_git_source", lambda *args: BASE)
    """Actual incident #1573: old source CI green, but current main conflicts.

    The source must stay recoverable; the adopter must select another valid
    candidate instead of retrying stale byte-for-byte protected integration.
    """
    old = source_pr(n=1573)
    next_sha = "c" * 40
    newer = source_pr(n=2156, sha=next_sha)
    examined = []

    def fetch(path):
        examined.append(path)
        if path.startswith("pulls?state=open&base=main"):
            return [old, newer]
        if path.startswith("pulls?state=all&head="):
            return []
        if path in ("pulls/1573/files?per_page=100", "pulls/2156/files?per_page=100"):
            return source_files()
        if path == "pulls/1573":
            return dict(old, mergeable=False)
        if path == "pulls/2156":
            return dict(newer, mergeable=True)
        if path == f"commits/{next_sha}/check-runs?per_page=100":
            return source_checks(sha=next_sha)
        if path == f"compare/{next_sha}...{BASE}?per_page=100":
            return main_compare(source_sha=next_sha)
        raise AssertionError(f"unexpected source read: {path}")

    monkeypatch.setattr(adoption, "api", fetch)
    monkeypatch.setattr(adoption, "_adoption_candidate_window",
                        lambda rows, *, slot: ([old, newer], 1))
    receipt = adoption.discover(slot=0)
    assert receipt["status"] == "SOURCE_READY"
    assert (receipt["pr_number"], receipt["source_sha"]) == (2156, next_sha)
    assert receipt["can_execute"] is False
    assert f"commits/{SHA}/check-runs?per_page=100" not in examined


def test_conflicted_source_does_not_spend_implementation_writer_or_approval(monkeypatch):
    source = source_pr(n=1573)
    def fetch(path):
        if path.startswith("pulls?state=open&base=main"):
            return [source]
        if path.startswith("pulls?state=all&head="):
            return []
        if path == "pulls/1573/files?per_page=100":
            return source_files()
        if path == "pulls/1573":
            return dict(source, mergeable=False)
        raise AssertionError(f"no checks, adoption or code should be attempted: {path}")
    monkeypatch.setattr(adoption, "api", fetch)
    result = adoption.discover(slot=0)
    assert result["status"] == "NO_ELIGIBLE_SOURCE"
    assert result["can_execute"] is False


def test_discovery_defers_true_mergeable_same_path_drift_and_advances(monkeypatch):
    # This case exercises candidate identity/rotation, not local git IO.
    monkeypatch.setattr(adoption, "verify_git_source", lambda *args: BASE)
    """GitHub's mergeable=true does NOT imply our exact-source-copy is safe."""
    old = source_pr(n=1573)
    next_sha = "c" * 40
    newer = source_pr(n=2158, sha=next_sha)
    observed = []

    def fetch(path):
        observed.append(path)
        if path.startswith("pulls?state=open&base=main"):
            return [old, newer]
        if path.startswith("pulls?state=all&head="):
            return []
        if path in ("pulls/1573/files?per_page=100", "pulls/2158/files?per_page=100"):
            return source_files()
        if path == "pulls/1573":
            return dict(old, mergeable=True)
        if path == "pulls/2158":
            return dict(newer, mergeable=True)
        if path == f"commits/{SHA}/check-runs?per_page=100":
            return source_checks()
        if path == f"commits/{next_sha}/check-runs?per_page=100":
            return source_checks(sha=next_sha)
        if path == f"compare/{SHA}...{BASE}?per_page=100":
            return main_compare(changes=[
                ".github/workflows/wow-v17-engineering-provider-dispatcher.yml"
            ])
        if path == f"compare/{next_sha}...{BASE}?per_page=100":
            return main_compare(source_sha=next_sha, changes=[
                "artifacts/wow-engine/v17/unrelated.py"
            ])
        raise AssertionError(path)

    monkeypatch.setattr(adoption, "api", fetch)
    monkeypatch.setattr(adoption, "_adoption_candidate_window",
                        lambda rows, *, slot: ([old, newer], 1))
    result = adoption.discover(slot=0)
    assert (result["status"], result["pr_number"]) == ("SOURCE_READY", 2158)
    assert result["can_execute"] is False
    assert f"compare/{SHA}...{BASE}?per_page=100" in observed


def test_true_mergeable_but_main_source_path_changed_never_claims_writer(monkeypatch):
    old = source_pr(n=1573)
    def fetch(path):
        if path.startswith("pulls?state=open&base=main"):
            return [old]
        if path.startswith("pulls?state=all&head="):
            return []
        if path == "pulls/1573/files?per_page=100":
            return source_files()
        if path == "pulls/1573":
            return old
        if path == f"commits/{SHA}/check-runs?per_page=100":
            return source_checks()
        if path == f"compare/{SHA}...{BASE}?per_page=100":
            return main_compare(changes=[
                ".github/workflows/wow-v17-engineering-provider-dispatcher.yml"])
        raise AssertionError(path)

    monkeypatch.setattr(adoption, "api", fetch)
    result = adoption.discover(slot=0)
    assert result["status"] == "NO_ELIGIBLE_SOURCE"
    assert result["can_execute"] is False


@pytest.mark.parametrize("compare_override", [
    {"files": None},
    {"files": [{}] * 300},
    {"base_commit": {"sha": "a" * 39}},
    {"merge_base_commit": None},
    {"status": "unexpected"},
    {"files": [{"filename": 33}]},
])
def test_incomplete_or_truncated_main_diff_never_admits(compare_override, monkeypatch):
    evidence = main_compare()
    evidence.update(compare_override)
    monkeypatch.setattr(adoption, "api", lambda _: evidence)
    with pytest.raises(adoption.AdoptionHold):
        adoption._source_paths_unchanged_on_main(
            source_sha=SHA, main_sha=BASE,
            paths=[".github/workflows/wow-v17-engineering-provider-dispatcher.yml"])


def test_main_renamed_path_blocks_exact_source_replay(monkeypatch):
    evidence = main_compare()
    evidence["files"] = [{"filename": "new-file.py",
                          "previous_filename": ".github/workflows/wow-v17-engineering-provider-dispatcher.yml"}]
    monkeypatch.setattr(adoption, "api", lambda _: evidence)
    with pytest.raises(adoption.AdoptionHold, match="SOURCE_PATH_DRIFT_DEFERRED"):
        adoption._source_paths_unchanged_on_main(
            source_sha=SHA, main_sha=BASE, paths=[
                ".github/workflows/wow-v17-engineering-provider-dispatcher.yml"])


def test_unknown_github_mergeability_is_not_authority(monkeypatch):
    old = source_pr(n=1573)
    def fetch(path):
        if path.startswith("pulls?state=open&base=main"):
            return [old]
        if path.startswith("pulls?state=all&head="):
            return []
        if path == "pulls/1573/files?per_page=100":
            return source_files()
        if path == "pulls/1573":
            return dict(old, mergeable=None)
        raise AssertionError("no provider check or writer for unknown mergeable status: " + path)
    monkeypatch.setattr(adoption, "api", fetch)
    assert adoption.discover(slot=0)["status"] == "NO_ELIGIBLE_SOURCE"


def test_app_replay_carries_source_canonical_incident_without_forging_review():
    pr = source_pr(n=2158)
    assert adoption.canonical_source_incident(pr) == "2136"
    actual_source = (ROOT / "artifacts/wow-engine/v17/engineering_authorized_adoption.py").read_text()
    assert 'f"Incident: #{incident}\\n\\n"' in actual_source
    assert "canonical_source_incident(current_source) != incident" in actual_source
    assert "SOURCE_INCIDENT_MOVED_BEFORE_PUSH_HOLD" in actual_source
    assert "independent_source_review_complete" in actual_source
    assert '"can_execute": False' in actual_source


@pytest.mark.parametrize("body", [
    None, "", "Parent: #1688", "Incident: #0", "Incident: #abc",
    "Incident: #2136\nIncident: #1688", "Incident: #2136 extra",
])
def test_missing_or_ambiguous_source_incident_does_not_consume_writer(body):
    pr = source_pr(n=2158)
    pr["body"] = body
    with pytest.raises(adoption.AdoptionHold, match="SOURCE_CANONICAL_INCIDENT_"):
        adoption.canonical_source_incident(pr)


def test_source_incident_matches_governance_certifier_parsing():
    for variant in ["Incident: #2136", "Incident: 2136", "  Incident: #2136  "]:
        assert adoption.canonical_source_incident({"body": variant}) == "2136"
    with pytest.raises(adoption.AdoptionHold, match="SOURCE_CANONICAL_INCIDENT_"):
        adoption.canonical_source_incident({"body": 2136})


def test_source_git_diff_check_rejects_whitespace_without_app_writer(monkeypatch):
    import subprocess
    seen = []
    def checked_run(args, *, check, capture_output, text):
        seen.append(args)
        assert check is False and capture_output is True and text is True
        return subprocess.CompletedProcess(args, 2, "", "whitespace error")
    monkeypatch.setattr(adoption.subprocess, "run", checked_run)
    with pytest.raises(adoption.AdoptionHold, match="SOURCE_PATCH_WHITESPACE_OR_GIT_CHECK_HOLD"):
        adoption.check_source_patch_whitespace(BASE, SHA, [
            "artifacts/wow-engine/v17/engineering_authorized_adoption.py"])
    assert seen and seen[0][:3] == ["git", "diff", "--check"]
    assert seen[0][3:5] == [BASE, SHA]


def test_source_git_diff_check_accepts_clean_replay(monkeypatch):
    import subprocess
    monkeypatch.setattr(adoption.subprocess, "run", lambda args, **kwargs:
                        subprocess.CompletedProcess(args, 0, "", ""))
    assert adoption.check_source_patch_whitespace(BASE, SHA, [
        "artifacts/wow-engine/v17/engineering_authorized_adoption.py"]) is None


def test_git_check_failure_deferred_before_single_writer(monkeypatch):
    """Reproduce #1635: exact source CI passes but git --check rejects the patch."""
    old = source_pr(n=1635)
    next_sha = "c" * 40
    good = source_pr(n=2158, sha=next_sha)
    checked = []

    def fetch(path):
        if path.startswith("pulls?state=open&base=main"):
            return [old, good]
        if path.startswith("pulls?state=all&head="):
            return []
        if path in ("pulls/1635/files?per_page=100", "pulls/2158/files?per_page=100"):
            return source_files()
        if path == "pulls/1635":
            return old
        if path == "pulls/2158":
            return good
        if path == f"commits/{SHA}/check-runs?per_page=100":
            return source_checks()
        if path == f"commits/{next_sha}/check-runs?per_page=100":
            return source_checks(sha=next_sha)
        if path == f"compare/{SHA}...{BASE}?per_page=100":
            return main_compare()
        if path == f"compare/{next_sha}...{BASE}?per_page=100":
            return main_compare(source_sha=next_sha)
        raise AssertionError(path)

    def check_git(base, main, candidate, paths):
        checked.append(candidate)
        if candidate == SHA:
            raise adoption.AdoptionHold("SOURCE_PATCH_WHITESPACE_OR_GIT_CHECK_HOLD")
        return BASE

    monkeypatch.setattr(adoption, "api", fetch)
    monkeypatch.setattr(adoption, "verify_git_source", check_git)
    monkeypatch.setattr(adoption, "_adoption_candidate_window",
                        lambda rows, *, slot: ([old, good], 1))
    result = adoption.discover(slot=0)
    assert (result["status"], result["pr_number"]) == ("SOURCE_READY", 2158)
    assert checked == [SHA, next_sha]
    assert result["can_execute"] is False


def test_source_whitespace_failure_alone_never_claims_a_writer(monkeypatch):
    old = source_pr(n=1635)
    def fetch(path):
        if path.startswith("pulls?state=open&base=main"):
            return [old]
        if path.startswith("pulls?state=all&head="):
            return []
        if path == "pulls/1635/files?per_page=100":
            return source_files()
        if path == "pulls/1635":
            return old
        if path == f"commits/{SHA}/check-runs?per_page=100":
            return source_checks()
        if path == f"compare/{SHA}...{BASE}?per_page=100":
            return main_compare()
        raise AssertionError(path)
    monkeypatch.setattr(adoption, "api", fetch)
    def no_writer(*args):
        raise adoption.AdoptionHold("SOURCE_PATCH_WHITESPACE_OR_GIT_CHECK_HOLD")
    monkeypatch.setattr(adoption, "verify_git_source", no_writer)
    result = adoption.discover(slot=0)
    assert result["status"] == "NO_ELIGIBLE_SOURCE"
    assert result["can_execute"] is False


def test_discovery_binds_advanced_main_instead_of_historical_pr_base(monkeypatch):
    """Main moved after source fork: a CI-green PR must be checked against LIVE main."""
    current_main = "d" * 40
    assert current_main != BASE
    candidate = source_pr(n=2158)
    checked = []
    requests = []

    def fetch(endpoint):
        requests.append(endpoint)
        if endpoint.startswith("pulls?state=open&base=main"):
            return [candidate]
        if endpoint.startswith("pulls?state=all&head="):
            return []
        if endpoint == "pulls/2158/files?per_page=100":
            return source_files()
        if endpoint == "pulls/2158":
            return candidate
        if endpoint == f"commits/{SHA}/check-runs?per_page=100":
            return source_checks()
        if endpoint == f"compare/{SHA}...{current_main}?per_page=100":
            # The historical merge base is not the checkout's current main.
            return main_compare(source_sha=SHA, main_sha=BASE)
        raise AssertionError(f"comparison used stale PR base instead of current main: {endpoint}")

    def verify(source_base, actual_main, source_head, paths):
        checked.append((source_base, actual_main, source_head))
        assert source_base == BASE
        assert actual_main == current_main
        assert source_head == SHA
        assert paths == sorted(row["filename"] for row in source_files())
        return BASE

    monkeypatch.setattr(adoption, "api", fetch)
    monkeypatch.setattr(adoption, "verify_git_source", verify)
    result = adoption.discover(slot=0, protected_main_sha=current_main)
    assert result["status"] == "SOURCE_READY"
    assert result["pr_number"] == 2158 and result["can_execute"] is False
    assert checked == [(BASE, current_main, SHA)]
    assert f"compare/{SHA}...{current_main}?per_page=100" in requests


def test_discovery_rejects_same_path_drift_after_main_advances(monkeypatch):
    """Same old PR base, but current main now touched its protected file."""
    current_main = "d" * 40
    candidate = source_pr(n=2158)

    def fetch(endpoint):
        if endpoint.startswith("pulls?state=open&base=main"):
            return [candidate]
        if endpoint.startswith("pulls?state=all&head="):
            return []
        if endpoint == "pulls/2158/files?per_page=100":
            return source_files()
        if endpoint == "pulls/2158":
            return candidate
        if endpoint == f"commits/{SHA}/check-runs?per_page=100":
            return source_checks()
        if endpoint == f"compare/{SHA}...{current_main}?per_page=100":
            return main_compare(source_sha=SHA, main_sha=BASE,
                                changes=[source_files()[0]["filename"]])
        raise AssertionError(endpoint)

    monkeypatch.setattr(adoption, "api", fetch)
    monkeypatch.setattr(
        adoption, "verify_git_source",
        lambda *args: pytest.fail("source drift must stop before Git/writer"),
    )
    result = adoption.discover(slot=0, protected_main_sha=current_main)
    assert result["status"] == "NO_ELIGIBLE_SOURCE"
    assert result["can_execute"] is False


def test_discovery_invalid_protected_main_fails_before_inventory(monkeypatch):
    monkeypatch.setattr(adoption, "api", lambda _: pytest.fail("no GitHub reads"))
    with pytest.raises(adoption.AdoptionHold, match="DISCOVERY_MAIN_SHA_INVALID_HOLD"):
        adoption.discover(slot=0, protected_main_sha="not-a-commit")


def test_protected_main_cli_passes_real_checked_out_sha_to_discovery(monkeypatch, capsys):
    import json
    import sys
    live = "d" * 40
    observed = []

    def read_only_git(*args):
        observed.append(args)
        assert args == ("git", "rev-parse", "refs/remotes/origin/main")
        return live

    monkeypatch.setattr(adoption, "call", read_only_git)
    monkeypatch.setattr(adoption, "discover", lambda **kwargs: {
        "status": "NO_ELIGIBLE_SOURCE",
        "main_binding": kwargs["protected_main_sha"],
        "can_execute": False,
    })
    monkeypatch.setattr(sys, "argv", ["engineering_authorized_adoption.py", "discover",
                                     "--repo", REPO])
    adoption.main()
    result = json.loads(capsys.readouterr().out)
    assert result["main_binding"] == live
    assert result["can_execute"] is False
    assert observed == [("git", "rev-parse", "refs/remotes/origin/main")]


def test_adoption_api_quota_failover_is_get_only_and_separately_authenticated(monkeypatch):
    """Only a typed GitHub installation-rate 403 may switch GET transport."""
    import subprocess

    monkeypatch.setenv("GH_TOKEN", "limited-read")
    monkeypatch.setenv("APP_TOKEN", "distinct-engineering-app")
    seen = []

    def fake_run(argv, **kwargs):
        assert argv == ["gh", "api", "/repos/" + REPO + "/pulls/2251"]
        assert kwargs["capture_output"] is True
        assert kwargs["check"] is False
        assert kwargs["timeout"] == 45
        identity = kwargs["env"]["GH_TOKEN"]
        seen.append(identity)
        if identity == "limited-read":
            return subprocess.CompletedProcess(
                argv, 1, "", "gh: API rate limit exceeded for installation (HTTP 403)"
            )
        return subprocess.CompletedProcess(argv, 0, '{"number":2251}', "")

    monkeypatch.setattr(adoption.subprocess, "run", fake_run)
    assert adoption.api("pulls/2251") == {"number": 2251}
    assert seen == ["limited-read", "distinct-engineering-app"]


@pytest.mark.parametrize("stderr", [
    "HTTP 403 Resource not accessible by integration",
    "HTTP 403 Permission denied by policy",
    "gh: API rate limit exceeded for installation (HTTP 429)",
    "HTTP 401 Invalid credentials",
])
def test_adoption_api_never_fails_over_on_untyped_permission_denial(monkeypatch, stderr):
    import subprocess

    monkeypatch.setenv("GH_TOKEN", "read-token")
    monkeypatch.setenv("APP_TOKEN", "write-token")
    used = []

    def fake_run(argv, **kwargs):
        used.append(kwargs["env"]["GH_TOKEN"])
        return subprocess.CompletedProcess(argv, 1, "", stderr)

    monkeypatch.setattr(adoption.subprocess, "run", fake_run)
    with pytest.raises(adoption.AdoptionHold, match="GITHUB_EVIDENCE_READ_DENIED_OR_UNAVAILABLE_HOLD"):
        adoption.api("pulls/2251")
    assert used == ["read-token"]


@pytest.mark.parametrize("secondary,expected", [
    (None, "GITHUB_INSTALLATION_QUOTA_DEFERRED_HOLD"),
    ("read-token", "GITHUB_INSTALLATION_QUOTA_DEFERRED_HOLD"),
    ("write-token", "GITHUB_ALTERNATE_READ_UNAVAILABLE_HOLD"),
])
def test_adoption_api_dual_quota_or_missing_backup_holds(monkeypatch, secondary, expected):
    import subprocess

    monkeypatch.setenv("GH_TOKEN", "read-token")
    if secondary is None:
        monkeypatch.delenv("APP_TOKEN", raising=False)
    else:
        monkeypatch.setenv("APP_TOKEN", secondary)
    identities = []

    def fake_run(argv, **kwargs):
        identities.append(kwargs["env"]["GH_TOKEN"])
        return subprocess.CompletedProcess(
            argv, 1, "", "API rate limit exceeded for installation (HTTP 403)"
        )

    monkeypatch.setattr(adoption.subprocess, "run", fake_run)
    with pytest.raises(adoption.AdoptionHold, match=expected):
        adoption.api("pulls/2251")
    assert identities == (["read-token", "write-token"] if secondary == "write-token"
                          else ["read-token"])


@pytest.mark.parametrize("body", ["<html>error</html>", "null", "true", '"string"'])
def test_adoption_api_malformed_or_unexpected_json_holds(monkeypatch, body):
    import subprocess

    monkeypatch.setenv("GH_TOKEN", "read-token")
    monkeypatch.setattr(
        adoption.subprocess, "run",
        lambda argv, **kw: subprocess.CompletedProcess(argv, 0, body, "")
    )
    with pytest.raises(adoption.AdoptionHold, match="GITHUB_EVIDENCE_RESPONSE_"):
        adoption.api("pulls/2251")


def test_adoption_api_rejects_unsafe_endpoint_without_any_request(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "read-token")
    monkeypatch.setattr(
        adoption.subprocess, "run",
        lambda *args, **kw: pytest.fail("unsafe endpoint made network request")
    )
    for endpoint in ("/pulls", "../pulls", "pulls/../approve", "pulls//2251", ""):
        with pytest.raises(adoption.AdoptionHold, match="GITHUB_EVIDENCE_ENDPOINT_INVALID_HOLD"):
            adoption.api(endpoint)
