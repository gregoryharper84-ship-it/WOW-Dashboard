"""Fail-closed authenticated SIRT review evidence; never model promotion."""
from v17.sirt_review_evidence import REQUIRED, ACTIONS_APP, evaluate

SHA = "a" * 40
REPO = "gregoryharper84-ship-it/WOW-Dashboard"


def case():
    pr = {
        "head": {"sha": SHA, "repo": {"full_name": REPO}},
        "base": {"ref": "main"},
        "state": "open", "draft": False, "merged_at": None,
        "user": {"id": 101, "login": "author"},
    }
    reviews = [{
        "id": 71, "state": "APPROVED", "commit_id": SHA,
        "submitted_at": "2026-11-18T18:00:00Z",
        "user": {"id": 202, "login": "independent-sirt", "type": "User"},
    }]
    checks = [
        {"id": i + 1, "name": name, "head_sha": SHA, "app": {"id": ACTIONS_APP},
         "status": "completed", "conclusion": "success"}
        for i, name in enumerate(REQUIRED)
    ]
    return dict(
        repository=REPO, expected_sha=SHA, pr=pr,
        reviews=reviews, check_runs=checks,
        authorized_reviewer_ids={202}, owner_id=303, head_commit_author_id=101,
    )


def hold(c, reason):
    result = evaluate(**c)
    assert result["review_status"] == "HOLD"
    assert reason in result["findings"]
    assert result["can_execute"] is False
    assert result["model_certified"] is False
    assert result["merge_or_release_authorized"] is False


def test_ready_is_evidence_only_not_certification():
    result = evaluate(**case())
    assert result["review_status"] == "EVIDENCE_READY"
    assert result["independent_scientific_acceptance_required"] is True
    assert result["protected_release_authority_required"] is True
    assert result["merge_or_release_authorized"] is False
    assert result["model_certified"] is False
    assert result["can_execute"] is False


def test_reviewer_is_author():
    c = case()
    c["pr"]["user"]["id"] = 202
    hold(c, "SIRT_REVIEWER_NOT_INDEPENDENT")


def test_reviewer_is_owner():
    c = case()
    c["owner_id"] = 202
    hold(c, "SIRT_REVIEWER_NOT_INDEPENDENT")


def test_reviewer_is_head_commit_author():
    c = case()
    c["head_commit_author_id"] = 202
    hold(c, "SIRT_REVIEWER_NOT_INDEPENDENT")


def test_stale_review_sha():
    c = case()
    c["reviews"][0]["commit_id"] = "b" * 40
    hold(c, "SIRT_INDEPENDENT_EXACT_HEAD_APPROVAL_MISSING")


def test_changed_head():
    c = case()
    c["pr"]["head"]["sha"] = "b" * 40
    hold(c, "SIRT_PR_IDENTITY_OR_HEAD_INVALID")


def test_latest_changes_requested():
    c = case()
    c["reviews"].append(dict(c["reviews"][0], id=72, state="CHANGES_REQUESTED"))
    hold(c, "SIRT_INDEPENDENT_EXACT_HEAD_APPROVAL_MISSING")


def test_dismissed_review():
    c = case()
    c["reviews"].append(dict(c["reviews"][0], id=72, state="DISMISSED"))
    hold(c, "SIRT_INDEPENDENT_EXACT_HEAD_APPROVAL_MISSING")


def test_wrong_app_for_required_check():
    c = case()
    c["check_runs"][0]["app"]["id"] = 999
    hold(c, "SIRT_REQUIRED_CHECK_MISSING:" + REQUIRED[0])


def test_failing_latest_check():
    c = case()
    c["check_runs"].append(dict(c["check_runs"][0], id=99, conclusion="failure"))
    hold(c, "SIRT_REQUIRED_CHECK_NOT_GREEN:" + REQUIRED[0])


def test_unbound_reviewer():
    c = case()
    c["authorized_reviewer_ids"] = set()
    hold(c, "SIRT_REVIEWER_IDENTITY_NOT_BOUND")


def test_ambiguous_reviewer_principals():
    c = case()
    c["authorized_reviewer_ids"] = {202, 204}
    hold(c, "SIRT_REVIEWER_IDENTITY_NOT_BOUND")


def test_draft():
    c = case()
    c["pr"]["draft"] = True
    hold(c, "SIRT_PR_IDENTITY_OR_HEAD_INVALID")


def test_foreign_repository():
    c = case()
    c["pr"]["head"]["repo"]["full_name"] = "foreign/repo"
    hold(c, "SIRT_PR_IDENTITY_OR_HEAD_INVALID")


def test_missing_required_check():
    c = case()
    c["check_runs"].pop()
    hold(c, "SIRT_REQUIRED_CHECK_MISSING:" + REQUIRED[-1])


def test_unidentified_head_commit_author():
    c = case()
    c["head_commit_author_id"] = None
    hold(c, "SIRT_REVIEWER_IDENTITY_NOT_BOUND")


def test_nonstring_expected_sha_is_typed_hold():
    c = case()
    c["expected_sha"] = None
    assert evaluate(**c)["code"] == "SIRT_TARGET_INVALID"


def test_malformed_head_dict_is_typed_hold():
    c = case()
    c["pr"]["head"] = "garbled"
    hold(c, "SIRT_PR_IDENTITY_OR_HEAD_INVALID")


def test_malformed_check_app_is_missing_trusted_check():
    c = case()
    c["check_runs"][0]["app"] = "forged"
    hold(c, "SIRT_REQUIRED_CHECK_MISSING:" + REQUIRED[0])
