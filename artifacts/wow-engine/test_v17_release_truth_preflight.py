"""Regression suite for live-first, non-gating release truth reporting."""
from v17.release_truth_preflight import applies_to_main, classify

HEAD = "a" * 40
APP_QA = 5245832
APP_RELEASE = 5245853
APP_NATIVE = 15368


def rule(*, count=0):
    return {
        "enforcement": "active", "target": "branch",
        "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
        "rules": [
            {"type": "pull_request", "parameters": {
                "required_approving_review_count": count}},
            {"type": "required_status_checks", "parameters": {
                "strict_required_status_checks_policy": True,
                "required_status_checks": [
                    {"context": "WOW Independent QA exact-head", "integration_id": APP_QA},
                    {"context": "WOW Release Authority exact-head", "integration_id": APP_RELEASE},
                    {"context": "WOW required-three regression", "integration_id": APP_NATIVE},
                    {"context": "WOW governed probability backend", "integration_id": APP_NATIVE},
                    {"context": "WOW additional required regression", "integration_id": APP_NATIVE},
                ]}},
        ],
    }


def pr(state="clean"):
    return {"number": 2424, "state": "open", "draft": False,
            "mergeable_state": state, "head": {"sha": HEAD},
            "base": {"ref": "main"}}


def checks():
    return [
        {"id": i, "head_sha": HEAD, "name": row["context"],
         "app": {"id": row["integration_id"]},
         "status": "completed", "conclusion": "success"}
        for i, row in enumerate(rule()["rules"][1]["parameters"]["required_status_checks"], 1)
    ]


def report(p=None, rules=None, cc=None):
    return classify(
        pr() if p is None else p,
        [rule()] if rules is None else rules,
        checks() if cc is None else cc,
        repository="gregoryharper84-ship-it/WOW-Dashboard",
    )


def names(r):
    return [x["finding"] for x in r["findings"]]


def test_2424_rejects_obsolete_human_review_claim_under_live_zero_review_ruleset():
    r = report()
    assert r["ruleset_human_review"] == "NOT_REQUIRED_BY_RULESET"
    assert r["rejected_obsolete_claims"] == ["HUMAN_REVIEW_REQUIRED_BY_ACTIVE_RULESET"]
    assert len(r["required_check_identities"]) == 5
    assert r["findings"] == []
    assert r["can_execute"] is False
    assert r["not_release_authorization"] is True
    assert r["deployment"] == "NOT_ASSESSED"


def test_real_merge_conflict_is_blocker_not_obsolete_review():
    r = report(p=pr("dirty"))
    assert "PR_MERGE_CONFLICT" in names(r)
    assert r["ruleset_human_review"] == "NOT_REQUIRED_BY_RULESET"


def test_new_source_head_never_inherits_old_green_checks():
    old = checks()
    for item in old:
        item["head_sha"] = "b" * 40
    r = report(cc=old)
    assert sum(x.startswith("REQUIRED_CHECK_MISSING:") for x in names(r)) == 5


def test_wrong_app_cannot_forge_required_exact_head_check():
    fake = checks()
    fake[0]["app"]["id"] = 999
    r = report(cc=fake)
    assert "REQUIRED_CHECK_MISSING:WOW Independent QA exact-head:5245832" in names(r)


def test_newest_same_app_failure_outweighs_stale_pass_on_same_head():
    cc = checks()
    cc.append({**cc[0], "id": 100, "conclusion": "failure"})
    r = report(cc=cc)
    assert "REQUIRED_CHECK_NOT_SUCCESS:WOW Independent QA exact-head:5245832" in names(r)


def test_missing_policy_is_unverified_not_zero_review_requirement():
    r = report(rules=[])
    assert r["ruleset_human_review"] == "NOT_REQUIRED_BY_RULESET"
    r = classify(pr(), None, checks(), repository="a/b")
    assert r["ruleset_human_review"] == "UNVERIFIED"
    assert "RULESET_REQUIREMENTS_UNVERIFIED" in names(r)
    assert r["rejected_obsolete_claims"] == []


def test_unknown_rule_patterns_never_silently_claim_zero_approvals():
    rs = rule()
    rs["conditions"]["ref_name"]["exclude"] = ["release-*"]
    r = report(rules=[rs])
    assert r["ruleset_human_review"] == "UNVERIFIED"
    assert "RULESET_REQUIREMENTS_UNVERIFIED" in names(r)


def test_real_required_review_not_dismissed():
    r = report(rules=[rule(count=1)])
    assert r["ruleset_human_review"] == "REQUIRED"
    assert r["rejected_obsolete_claims"] == []


def test_missing_check_inventory_is_unverified():
    r = classify(pr(), [rule()], None, repository="a/b")
    assert "CHECK_INVENTORY_UNVERIFIED" in names(r)
    assert not any(x.startswith("REQUIRED_CHECK_MISSING") for x in names(r))


def test_unknown_mergeability_never_claims_clean():
    r = report(p=pr("unknown"))
    assert "PR_MERGEABILITY_UNVERIFIED" in names(r)


def test_main_exclusion_and_wrong_branch_scope():
    r = rule()
    r["conditions"]["ref_name"]["exclude"] = ["~DEFAULT_BRANCH"]
    assert applies_to_main(r) is False
    r["conditions"]["ref_name"]["exclude"] = []
    r["conditions"]["ref_name"]["include"] = ["refs/heads/feature"]
    assert applies_to_main(r) is False


def test_rule_list_corruption_fail_closed():
    r = rule()
    r["rules"][1]["parameters"]["required_status_checks"][0]["integration_id"] = None
    out = report(rules=[r])
    assert out["ruleset_human_review"] == "UNVERIFIED"
    assert "RULESET_REQUIREMENTS_UNVERIFIED" in names(out)


def test_non_main_target_is_not_mislabeled_as_main_release_ready():
    p = pr()
    p["base"]["ref"] = "development"
    out = report(p=p)
    assert "PR_TARGET_MAIN_UNVERIFIED" in names(out)


def test_latest_same_app_pass_retire_old_failed_receipt_from_blocker_report():
    old = checks()
    old[0]["conclusion"] = "failure"
    new = {**old[0], "id": 100, "conclusion": "success"}
    out = report(cc=old + [new])
    assert not any(x.startswith("REQUIRED_CHECK_NOT_SUCCESS:WOW Independent QA") for x in names(out))
    assert out["findings"] == []


def test_unreported_required_checks_are_pending_not_failed_or_denied():
    out = report(cc=[])
    assert out["merge_evidence"] == "PENDING"
    assert len(out["findings"]) == 5
    assert all(x["state"] == "PENDING" for x in out["findings"])


def test_running_required_check_is_pending_not_failure():
    cc = checks()
    cc[0]["status"] = "in_progress"
    cc[0]["conclusion"] = None
    out = report(cc=cc)
    assert "REQUIRED_CHECK_PENDING:WOW Independent QA exact-head:5245832" in names(out)
    assert out["merge_evidence"] == "PENDING"



def test_behind_main_blocked_only_when_effective_ruleset_requires_strict_base():
    out = report(p=pr("behind"))
    assert "PR_HEAD_BEHIND_STRICT_BASE" in names(out)
    assert out["merge_evidence"] == "BLOCKED"

    rs = rule()
    rs["rules"][1]["parameters"]["strict_required_status_checks_policy"] = False
    out = report(p=pr("behind"), rules=[rs])
    assert "PR_HEAD_BEHIND_STRICT_BASE" not in names(out)
    assert "PR_BEHIND_BASE_REQUIREMENT_UNVERIFIED" in names(out)


def test_ambiguous_github_blocked_state_is_unverified_not_self_certified():
    out = report(p=pr("blocked"))
    assert "PR_MERGEABILITY_UNVERIFIED" in names(out)
    assert out["merge_evidence"] == "UNVERIFIED"


def test_malformed_ruleset_structures_are_unverified_not_runtime_errors():
    assert applies_to_main(None) is None
    rs = rule()
    rs["conditions"] = []
    out = report(rules=[rs])
    assert out["ruleset_human_review"] == "UNVERIFIED"
    rs = rule()
    rs["rules"][0] = None
    out = report(rules=[rs])
    assert out["ruleset_human_review"] == "UNVERIFIED"


def test_zero_review_count_does_not_hide_additional_review_controls():
    for key, value in (
        ("require_code_owner_review", True),
        ("require_last_push_approval", True),
        ("required_reviewers", [{"file_pattern": "**"}]),
        ("required_review_thread_resolution", True),
    ):
        rs = rule()
        rs["rules"][0]["parameters"][key] = value
        out = report(rules=[rs])
        assert out["ruleset_human_review"] == "UNVERIFIED"
        assert out["rejected_obsolete_claims"] == []


def test_malformed_check_inventory_is_unverified_not_missing_checks():
    out = report(cc=[{"app": None, "id": 7}])
    assert "CHECK_INVENTORY_UNVERIFIED" in names(out)
    assert not any(x.startswith("REQUIRED_CHECK_MISSING:") for x in names(out))


def test_malformed_pr_identity_does_not_invent_real_blocker():
    p = pr()
    p["head"] = "corrupt"
    p.pop("state")
    out = report(p=p)
    assert "PR_HEAD_UNVERIFIED" in names(out)
    assert "PR_STATE_UNVERIFIED" in names(out)
    assert "PR_NOT_OPEN" not in names(out)


def test_skipped_and_neutral_required_checks_are_not_falsely_blocked():
    # GitHub allows these conclusions, but neither is proof of semantic review.
    for conclusion in ("skipped", "neutral"):
        cc = checks()
        cc[0]["conclusion"] = conclusion
        out = report(cc=cc)
        expected = "REQUIRED_CHECK_NON_SUCCESS_ACCEPTED_BY_GITHUB:WOW Independent QA exact-head:5245832"
        assert expected in names(out)
        assert out["merge_evidence"] == "UNVERIFIED"
        assert not any(x["state"] == "BLOCKED" for x in out["findings"])
        assert out["semantic_review"] == "NOT_ASSESSED"
        assert out["not_release_authorization"] is True


def test_failed_required_checks_remain_known_blockers():
    for conclusion in ("failure", "cancelled", "timed_out", "action_required", "stale"):
        cc = checks()
        cc[0]["conclusion"] = conclusion
        out = report(cc=cc)
        assert "REQUIRED_CHECK_NOT_SUCCESS:WOW Independent QA exact-head:5245832" in names(out)
        assert out["merge_evidence"] == "BLOCKED"


def test_engineering_digest_latest_app_check_is_selected_across_pages():
    import json
    import shutil
    import subprocess
    from pathlib import Path

    assert shutil.which("jq"), "jq is required by the digest workflow"
    workflow = (Path(__file__).resolve().parents[2]
                / ".github/workflows/wow-v17-engineering-digest.yml")
    script = workflow.read_text(encoding="utf-8")
    # Execute the exact deployed jq expression with two pages in one input.
    selector = script.split("summary=$(jq -sr '", 1)[1].split(
        "' <<<\"$checks\")", 1
    )[0]
    def select(pages):
        proc = subprocess.run(
            ["jq", "-sr", selector],
            input="\n".join(json.dumps(page) for page in pages),
            capture_output=True, text=True, check=True,
        )
        return proc.stdout.strip()
    def check(check_id, conclusion, message):
        return {"id": check_id, "name": "WOW Independent QA exact-head",
                "app": {"id": APP_QA}, "conclusion": conclusion,
                "output": {"summary": message}}
    old_failed = check(10, "failure", "OBSOLETE_HOLD")
    current_pass = check(50, "success", "CURRENT_PASS")
    newest_fail = check(99, "failure", "CURRENT_FAILURE")
    assert select([[current_pass], [old_failed]]) == ""
    assert select([[old_failed], [current_pass]]) == ""
    assert select([[old_failed], [newest_fail]]) == "CURRENT_FAILURE"
