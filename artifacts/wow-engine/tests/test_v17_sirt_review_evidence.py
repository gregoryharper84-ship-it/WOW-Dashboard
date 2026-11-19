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


def test_github_credential_cannot_be_forwarded_on_redirect():
    from v17.sirt_review_evidence import NoRedirect
    assert NoRedirect().redirect_request(
        None, None, 302, "redirect", {}, "https://attacker.example"
    ) is None


# Authenticated GitHub collection must return a typed HOLD for malformed
# provider records, never crash the protected independent QA producer.
def _authenticated_payloads():
    from copy import deepcopy
    c = deepcopy(case())
    return {
        "pulls/2732": c["pr"],
        "": {"owner": {"id": c["owner_id"]}},
        f"commits/{SHA}": {"author": {"id": c["head_commit_author_id"]}},
        "pulls/2732/reviews?per_page=100": c["reviews"],
        f"commits/{SHA}/check-runs?per_page=100": {
            "total_count": len(c["check_runs"]), "check_runs": c["check_runs"],
        },
    }


def _collect_from_authenticated_records(monkeypatch, data):
    from copy import deepcopy
    from v17 import sirt_review_evidence
    monkeypatch.setattr(
        sirt_review_evidence, "github_json",
        lambda path, token: deepcopy(data[path]),
    )
    return sirt_review_evidence.collect_and_evaluate(
        2732, SHA, 202, "local-test-credential",
    )


def test_collector_valid_authenticated_records_are_evidence_only(monkeypatch):
    result = _collect_from_authenticated_records(monkeypatch, _authenticated_payloads())
    assert result["review_status"] == "EVIDENCE_READY"
    assert result["model_certified"] is False
    assert result["merge_or_release_authorized"] is False


def test_collector_malformed_repo_owner_returns_typed_hold(monkeypatch):
    data = _authenticated_payloads()
    data[""]["owner"] = "invalid"
    result = _collect_from_authenticated_records(monkeypatch, data)
    assert result["review_status"] == "HOLD"
    assert result["code"] == "SIRT_EVIDENCE_INVALID"


def test_collector_malformed_head_commit_author_returns_typed_hold(monkeypatch):
    data = _authenticated_payloads()
    data[f"commits/{SHA}"]["author"] = "invalid"
    result = _collect_from_authenticated_records(monkeypatch, data)
    assert result["review_status"] == "HOLD"
    assert result["code"] == "SIRT_EVIDENCE_INVALID"


def test_collector_malformed_check_count_returns_typed_hold(monkeypatch):
    data = _authenticated_payloads()
    data[f"commits/{SHA}/check-runs?per_page=100"]["total_count"] = "invalid"
    result = _collect_from_authenticated_records(monkeypatch, data)
    assert result["review_status"] == "HOLD"
    assert result["code"] == "SIRT_EVIDENCE_INVALID"


def test_collector_truncated_check_inventory_returns_hold(monkeypatch):
    data = _authenticated_payloads()
    data[f"commits/{SHA}/check-runs?per_page=100"]["total_count"] = 999
    result = _collect_from_authenticated_records(monkeypatch, data)
    assert result["review_status"] == "HOLD"
    assert result["code"] == "SIRT_EVIDENCE_PAGINATION_INCOMPLETE"


def test_collector_changed_pr_head_returns_hold(monkeypatch):
    data = _authenticated_payloads()
    data["pulls/2732"]["head"]["sha"] = "b" * 40
    result = _collect_from_authenticated_records(monkeypatch, data)
    assert result["review_status"] == "HOLD"
    assert "SIRT_PR_IDENTITY_OR_HEAD_INVALID" in result["findings"]
