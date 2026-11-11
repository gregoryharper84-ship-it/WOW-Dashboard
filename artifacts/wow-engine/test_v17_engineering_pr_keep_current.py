from v17 import engineering_pr_keep_current as kc


def pr(
    number=1,
    state="open",
    draft=False,
    login=kc.ENGINEERING_BOT_LOGIN,
    mergeable_state="behind",
    head_sha="abc123",
):
    return {
        "number": number,
        "state": state,
        "draft": draft,
        "user": {"login": login},
        "mergeable_state": mergeable_state,
        "head": {"sha": head_sha},
    }


def test_behind_bot_pr_is_selected():
    plan = kc.select_prs_to_update([pr()])
    assert plan == [{"number": 1, "expected_head_sha": "abc123"}]


def test_clean_bot_pr_is_not_selected():
    plan = kc.select_prs_to_update([pr(mergeable_state="clean")])
    assert plan == []


def test_draft_bot_pr_is_not_selected():
    plan = kc.select_prs_to_update([pr(draft=True)])
    assert plan == []


def test_closed_pr_is_not_selected():
    plan = kc.select_prs_to_update([pr(state="closed")])
    assert plan == []


def test_other_author_pr_is_never_selected_even_when_behind():
    plan = kc.select_prs_to_update([pr(login="some-human", mergeable_state="behind")])
    assert plan == []


def test_other_author_pr_is_never_selected_even_when_dirty():
    # Negative test: other-author PRs are never touched regardless of mergeable_state.
    plan = kc.select_prs_to_update([pr(login="some-human", mergeable_state="dirty")])
    assert plan == []


def test_dirty_bot_pr_is_not_selected_only_behind_triggers_update():
    plan = kc.select_prs_to_update([pr(mergeable_state="dirty")])
    assert plan == []


def test_missing_head_sha_is_skipped():
    row = pr()
    row["head"] = {}
    assert kc.select_prs_to_update([row]) == []


def test_multiple_prs_only_eligible_ones_selected():
    rows = [
        pr(number=1, mergeable_state="behind"),
        pr(number=2, login="other-human", mergeable_state="behind"),
        pr(number=3, mergeable_state="clean"),
        pr(number=4, draft=True, mergeable_state="behind"),
    ]
    plan = kc.select_prs_to_update(rows)
    assert plan == [{"number": 1, "expected_head_sha": "abc123"}]


def test_custom_bot_login_is_respected():
    plan = kc.select_prs_to_update([pr(login="other-app[bot]")], bot_login="other-app[bot]")
    assert plan == [{"number": 1, "expected_head_sha": "abc123"}]


def test_conflict_comment_carries_typed_finding():
    comment = kc.conflict_comment(42)
    assert comment["finding"] == "PR_UPDATE_CONFLICT"
    assert "PR_UPDATE_CONFLICT" in comment["body"]
    assert "#42" in comment["body"]
    assert "can_execute=false" in comment["body"]


def test_classify_update_result_success():
    result = kc.classify_update_result(1, status_code=202)
    assert result == {"pr_number": 1, "outcome": "UPDATED", "comment": None}


def test_classify_update_result_conflict_409():
    result = kc.classify_update_result(7, status_code=409)
    assert result["outcome"] == "CONFLICT"
    assert result["comment"]["finding"] == "PR_UPDATE_CONFLICT"


def test_classify_update_result_conflict_422():
    result = kc.classify_update_result(7, status_code=422)
    assert result["outcome"] == "CONFLICT"


def test_classify_update_result_conflict_is_not_retried_as_update():
    # A conflict must never be silently retried/treated as success.
    result = kc.classify_update_result(7, status_code=422)
    assert result["outcome"] != "UPDATED"


def test_classify_update_result_unknown_failure():
    result = kc.classify_update_result(9, status_code=500)
    assert result == {"pr_number": 9, "outcome": "UNKNOWN_FAILURE", "comment": None}
