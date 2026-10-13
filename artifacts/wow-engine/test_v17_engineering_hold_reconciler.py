from v17.engineering_hold_reconciler import HoldDiscoveryError, select_hold

import pytest

REPO = "gregoryharper84-ship-it/WOW-Dashboard"
SHA = "a" * 40
QA = 5245832
RELEASE = 5245853


def pr(author="wow-engineering[bot]", draft=False):
    return {"number": 1683, "state": "open", "draft": draft,
            "user": {"login": author},
            "head": {"sha": SHA, "repo": {"full_name": REPO}},
            "base": {"ref": "main"}}


def check(name="WOW Independent QA exact-head", app=QA, sha=SHA,
          conclusion="failure", status="completed", cid=9,
          summary="Typed findings:\\n- `QA_TRUST_ROOT_OWNER_AUTHORED`"):
    return {"name": name, "app": {"id": app}, "head_sha": sha,
            "id": cid, "status": status, "conclusion": conclusion,
            "output": {"summary": summary}}


def discover(pulls=None, checks=None, **over):
    return select_hold(
        pulls if pulls is not None else [pr()],
        checks if checks is not None else {SHA: [check()]},
        repository=REPO, qa_app_id=over.get("qa_app_id", QA),
        release_app_id=over.get("release_app_id", RELEASE),
    )


def test_owner_authored_trust_root_routes_only_to_reproposal():
    result = discover(pulls=[pr(author=REPO.split("/")[0], draft=True)])
    assert result["eligible"] is True
    assert result["author"] == "gregoryharper84-ship-it"
    assert result["head_sha"] == SHA
    assert result["can_execute"] is False


def test_engineering_app_authored_failure_is_repair_candidate():
    result = discover(checks={SHA: [check(summary="Typed findings:\\n- `QA_EVIDENCE_FAILED`")]})
    assert result["eligible"] is True


@pytest.mark.parametrize("case", ["foreign_app", "wrong_sha", "success", "in_progress",
                                  "untrusted_owner", "fork"])
def test_untrusted_or_nonfailing_evidence_does_not_route(case):
    pull = pr()
    run = check()
    if case == "foreign_app":
        run["app"]["id"] = 15368
    if case == "wrong_sha":
        run["head_sha"] = "b" * 40
    if case == "success":
        run["conclusion"] = "success"
    if case == "in_progress":
        run["status"] = "in_progress"
    if case == "untrusted_owner":
        pull["user"]["login"] = REPO.split("/")[0]
        run["output"]["summary"] = "Typed findings:\\n- `QA_EVIDENCE_FAILED`"
    if case == "fork":
        pull["head"]["repo"]["full_name"] = "attacker/fork"
    assert discover([pull], {SHA: [run]})["eligible"] is False


def test_newer_pending_check_cannot_be_bypassed_with_old_failure():
    older = check(cid=8)
    newest = check(cid=9, status="in_progress")
    assert discover(checks={SHA: [older, newest]})["eligible"] is False


def test_release_check_must_come_from_its_own_app():
    runs = [check(name="WOW Release Authority exact-head", app=RELEASE, cid=10)]
    assert discover(checks={SHA: runs})["check_name"] == "WOW Release Authority exact-head"
    runs[0]["app"]["id"] = QA
    assert discover(checks={SHA: runs})["eligible"] is False


def test_missing_checks_fail_closed():
    with pytest.raises(HoldDiscoveryError, match="CHECKS_UNREADABLE"):
        discover(checks={})


def test_role_ids_cannot_collapse_or_be_unbound():
    with pytest.raises(HoldDiscoveryError, match="ROLE_APP_IDS_UNBOUND_OR_NOT_DISTINCT"):
        discover(release_app_id=QA)


def test_failure_without_typed_evidence_fails_closed():
    with pytest.raises(HoldDiscoveryError, match="ROLE_FAILURE_WITHOUT_TYPED_FINDINGS"):
        discover(checks={SHA: [check(summary="No typed findings.")]})


def test_zero_mutation_authority_in_no_action_result():
    assert discover(pulls=[]) == {"eligible": False, "scanned": 0, "can_execute": False}


from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/wow-v17-engineering-auto-repair.yml"


def test_recovery_uses_pr_head_not_source_workflow_sha():
    workflow = WORKFLOW.read_text()
    assert "engineering_hold_reconciler.py" in workflow
    assert '--qa-app-id "$QA_APP_ID"' in workflow
    assert '--release-app-id "$RELEASE_APP_ID"' in workflow
    assert "SOURCE_AUTHOR" in workflow
    assert "SUMMARY_B64" in workflow
    assert "base64 --decode" in workflow


def test_workflow_stays_read_only_until_engineering_app_and_never_authorizes_release():
    workflow = WORKFLOW.read_text()
    assert "ref: main" in workflow
    assert "Mint wow-engineering App token" in workflow
    assert "Re-propose protected source under Engineering App as draft only" in workflow
    assert "REPROPOSE_WITH_ENGINEERING_APP" in workflow
    assert "engineering_app_reproposal.py" in workflow
    assert "--write" in workflow
    assert "UNREVIEWED_REPROPOSAL_SOURCE" in workflow
    assert ".owner_approval_required == true" in workflow
    assert "gh pr merge" not in workflow
    assert "trigger_deploy" not in workflow


def test_newer_success_supersedes_stale_qa_failure():
    older = check(cid=8)
    newer = check(name="WOW Release Authority exact-head", app=RELEASE,
                  cid=9, status="completed", conclusion="success")
    assert discover(checks={SHA: [older, newer]})["eligible"] is False


def test_bounded_scan_does_not_lose_existing_pr_identity():
    # Bounded WIP: no unbounded API scans or automated dispatch loops.
    many = [dict(pr(), number=i) for i in range(25)]
    result = select_hold(many, {SHA: [check()]}, repository=REPO,
                         qa_app_id=QA, release_app_id=RELEASE, max_targets=1)
    assert result["eligible"] is True
    assert result["pr_number"] == 0
    assert result["scanned"] == 1
