"""Read-only GitHub REST schema fixtures; never publish checks or deploy."""
from copy import deepcopy
from pathlib import Path

import pytest

from v17 import github_release_journal as journal
from v17 import simple_release_policy_candidate as candidate


MAIN = "b" * 40
HEAD = "a" * 40
OTHER = "c" * 40
ROOT = "repos/" + candidate.REPOSITORY
ENV = ROOT + "/environments/wow-release"
RUN = ROOT + "/actions/runs/42"
RULESET = ROOT + f"/rulesets/{candidate.RULESET_ID}"
MAIN_REF = ROOT + "/git/ref/heads/main"
EFFECTIVE = ROOT + "/rules/branches/main?per_page=100&page=1"
HEAD_CHECKS = ROOT + f"/commits/{HEAD}/check-runs?per_page=100&page=1&filter=all"
MAIN_CHECKS = ROOT + f"/commits/{MAIN}/check-runs?per_page=100&page=1&filter=all"


def check(name, app, sha=HEAD, id=1):
    return {"id": id, "name": name, "head_sha": sha, "app": {"id": app},
            "status": "completed", "conclusion": "success"}


class GitHub:
    def __init__(self, policy=candidate.SINGLE_QA, route="human"):
        status = {
            "type": "required_status_checks",
            "parameters": {
                "strict_required_status_checks_policy": True,
                "do_not_enforce_on_create": False,
                "required_status_checks": [
                    {"context": n, "integration_id": 15368}
                    for n in candidate.REQUIRED_REGRESSION_CHECKS
                ] + [{"context": journal.QA_CHECK_NAME, "integration_id": journal.AUTHENTIC_QA_APP_ID}],
            },
        }
        checks = [check(n, 15368) for n in candidate.REQUIRED_REGRESSION_CHECKS]
        checks.append(check(journal.QA_CHECK_NAME, journal.AUTHENTIC_QA_APP_ID))
        if policy == candidate.LEGACY:
            status["parameters"]["required_status_checks"].append({
                "context": journal.RELEASE_CHECK_NAME, "integration_id": journal.AUTHENTIC_RELEASE_APP_ID,
            })
            checks.append(check(journal.RELEASE_CHECK_NAME, journal.AUTHENTIC_RELEASE_APP_ID))
        ruleset = {
            "id": candidate.RULESET_ID, "name": candidate.RULESET_NAME, "source": candidate.REPOSITORY,
            "source_type": "Repository", "target": "branch", "enforcement": "active", "bypass_actors": [],
            "conditions": {"ref_name": {"include": ["refs/heads/main"], "exclude": []}},
            "rules": [
                status, {"type": "deletion"}, {"type": "non_fast_forward"},
                {"type": "pull_request", "parameters": {
                    "require_code_owner_review": True, "dismiss_stale_reviews_on_push": True,
                    "require_last_push_approval": True,
                }},
            ],
        }
        reviewer = {"id": 200, "login": "independent-reviewer", "type": "User"}
        # Conditional positive fixture only: can_admins_bypass is NOT exposed
        # by the published REST response schema. Its absence must hold below.
        env = {
            "id": 10, "name": "wow-release", "can_admins_bypass": False,
            "deployment_branch_policy": {"protected_branches": False, "custom_branch_policies": True},
            "protection_rules": [{"type": "branch_policy"}],
        }
        apps = []
        if route == "human":
            env["protection_rules"].append({
                "type": "required_reviewers", "prevent_self_review": True,
                "reviewers": [{"type": "User", "reviewer": reviewer}],
            })
        else:
            apps = [{"id": 15, "enabled": True, "app": {"id": journal.AUTHENTIC_RELEASE_APP_ID,
                     "slug": "wow-release-authority", "node_id": "A_release", "integration_url": "https://example.invalid"}}]
        pr = {
            "number": 99, "merged": True, "merged_at": "2026-10-20T12:00:00Z",
            "merge_commit_sha": MAIN,
            "base": {"ref": "main", "repo": {"full_name": candidate.REPOSITORY}},
            "head": {"sha": HEAD, "repo": {"full_name": candidate.REPOSITORY}},
        }
        url = f"https://github.com/{candidate.REPOSITORY}/actions/runs/42"
        release = check(candidate.DEPLOYMENT_CHECK, journal.AUTHENTIC_RELEASE_APP_ID, MAIN)
        release.update(external_id=f"wow-release:42:1:{MAIN}", details_url=url,
                       completed_at="2026-10-20T13:01:00Z")
        self.data = {
            "user": {"id": 100, "login": "authenticated-collector"},
            RULESET: ruleset,
            EFFECTIVE: [dict(deepcopy(status), ruleset_id=candidate.RULESET_ID)],
            MAIN_REF: {"object": {"sha": MAIN}},
            ROOT + f"/commits/{MAIN}/pulls?per_page=100&page=1": [pr],
            ROOT + "/pulls/99": pr,
            HEAD_CHECKS: {
                "total_count": len(checks), "check_runs": checks,
            },
            RUN: {"id": 42, "run_attempt": 1, "event": "workflow_dispatch", "head_sha": MAIN,
                  "head_branch": "main", "path": ".github/workflows/" + journal.CONTROLLER_FILENAME,
                  "html_url": url, "run_started_at": "2026-10-20T13:00:00Z",
                  "repository": {"full_name": candidate.REPOSITORY},
                  "actor": {"id": 100}, "triggering_actor": {"id": 100}},
            ENV: env,
            ENV + "/deployment-branch-policies?per_page=100&page=1": {
                "total_count": 1, "branch_policies": [{"id": 1, "name": "main", "type": "branch"}],
            },
            ENV + "/deployment_protection_rules": {
                "total_count": len(apps), "custom_deployment_protection_rules": apps,
            },
            RUN + "/approvals": [{
                "state": "approved", "user": reviewer,
                "environments": [{"id": 10, "name": "wow-release"}], "comment": "Independently reviewed",
            }],
            ROOT + "/collaborators/independent-reviewer/permission": {
                "permission": "read", "user": reviewer,
            },
            MAIN_CHECKS: {
                "total_count": 1, "check_runs": [release],
            },
        }
        self.calls = []
        self.fault = None

    @property
    def checks(self):
        return self.data[HEAD_CHECKS]["check_runs"]

    def fetch(self, path):
        self.calls.append(path)
        if self.fault:
            self.fault(self, path)
        return (200, deepcopy(self.data[path])) if path in self.data else (404, None)

    def inspect(self):
        result = candidate.inspect_release_policy(target_sha=MAIN, run_id=42, fetch=self.fetch)
        assert result["post_allowed"] is False
        assert result["deploy_authorized"] is False
        assert result["merge_authorized"] is False
        assert result["can_execute"] is False
        assert result["DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"] is True
        assert result["runtime_generation"] == "V17_ACTIVE"
        assert result["terminal_authority"] == "V17_TERMINAL_REDUCER"
        assert result["custom_gpt_identity"] == "WOW_BETTING_ENGINE"
        assert result["status"] == "BLOCKED_WITH_EXACT_REASON"
        assert "RENDER_API_CREDENTIAL_SCOPE_UNATTESTED:#1875" in result["deployment_blockers"]
        return result


@pytest.mark.parametrize("policy,route", [
    (candidate.LEGACY, "human"), (candidate.SINGLE_QA, "human"), (candidate.SINGLE_QA, "app"),
])
def test_exact_head_policy_evidence_is_never_release_authority(policy, route):
    github = GitHub(policy, route)
    result = github.inspect()
    assert result["policy_evidence_valid"] is True
    assert result["observed_policy"] == policy
    assert not result["blockers"]
    if policy == candidate.LEGACY:
        assert ENV not in github.calls
        assert result["authorization_route"] is None
    else:
        assert result["authorization_route"] == (
            "independent_environment_reviewer" if route == "human"
            else "existing_release_app_deployment_decision"
        )


@pytest.mark.parametrize("field,value", [
    ("head_sha", OTHER), ("app", {"id": journal.AUTHENTIC_RELEASE_APP_ID}),
    ("app", None), ("conclusion", "failure"), ("conclusion", "neutral"),
    ("conclusion", "skipped"), ("status", "queued"), ("id", "malformed"),
])
@pytest.mark.parametrize("policy", [candidate.LEGACY, candidate.SINGLE_QA])
def test_qa_stale_foreign_failed_and_malformed_fail_closed(field, value, policy):
    github = GitHub(policy)
    github.checks[-1 if policy == candidate.SINGLE_QA else -2][field] = value
    assert github.inspect()["policy_evidence_valid"] is False


def test_new_pending_qa_does_not_reuse_old_pass():
    github = GitHub()
    github.checks.append(dict(github.checks[-1], id=2, status="in_progress", conclusion=None))
    github.data[HEAD_CHECKS]["total_count"] += 1
    assert github.inspect()["blockers"] == ["CHECK_NOT_SUCCESS:" + journal.QA_CHECK_NAME]


def test_nullable_app_and_missing_qa_have_exact_diagnostics():
    github = GitHub()
    github.checks[-1]["app"] = None
    assert github.inspect()["blockers"] == ["CHECK_MISSING_STALE_OR_FOREIGN:" + journal.QA_CHECK_NAME]
    github.checks.pop()
    github.data[HEAD_CHECKS]["total_count"] -= 1
    assert github.inspect()["blockers"] == ["CHECK_MISSING_STALE_OR_FOREIGN:" + journal.QA_CHECK_NAME]


@pytest.mark.parametrize("index", range(5))
def test_every_legacy_context_is_required(index):
    github = GitHub(candidate.LEGACY)
    del github.checks[index]
    github.data[HEAD_CHECKS]["total_count"] -= 1
    assert github.inspect()["policy_evidence_valid"] is False


@pytest.mark.parametrize("field,value", [
    ("enforcement", "evaluate"), ("id", 123), ("source", "foreign/repo"),
    ("target", "tag"), ("bypass_actors", [{"actor_type": "Integration", "actor_id": 1}]),
    ("conditions", {"ref_name": {"include": ["~ALL"], "exclude": []}}),
])
def test_authenticated_ruleset_mismatch_is_not_an_activation_flag(field, value):
    github = GitHub()
    github.data[RULESET][field] = value
    assert github.inspect()["blockers"] == ["ACTIVE_RULESET_MISMATCH"]


def test_foreign_ci_app_or_removed_codeowner_protection_is_rejected():
    github = GitHub()
    github.data[RULESET]["rules"][0]["parameters"]["required_status_checks"][0]["integration_id"] = 42
    assert github.inspect()["blockers"] == ["RULESET_REQUIRED_CONTEXT_MISMATCH"]
    github = GitHub()
    github.data[RULESET]["rules"][-1]["parameters"]["require_code_owner_review"] = False
    assert github.inspect()["blockers"] == ["RULESET_OWNER_PROTECTION_MISSING:require_code_owner_review"]


def test_effective_rules_must_match_including_other_active_status_rules():
    github = GitHub()
    github.data[EFFECTIVE].append(deepcopy(github.data[EFFECTIVE][0]))
    assert github.inspect()["blockers"] == ["EFFECTIVE_MAIN_RULESET_MISMATCH"]


@pytest.mark.parametrize("fault", ["main", "ruleset", "effective", "run", "environment", "branches", "app"])
def test_readback_drift_fails_closed(fault):
    github = GitHub()
    paths = {
        "main": MAIN_REF, "ruleset": RULESET, "effective": EFFECTIVE, "run": RUN,
        "environment": ENV, "branches": ENV + "/deployment-branch-policies?per_page=100&page=1",
        "app": ENV + "/deployment_protection_rules",
    }
    def drift(api, path):
        if path == paths[fault] and api.calls.count(path) == 2:
            if fault == "main":
                api.data[path]["object"]["sha"] = OTHER
            elif fault == "ruleset":
                api.data[path]["enforcement"] = "disabled"
            elif fault == "effective":
                api.data[path] = []
            elif fault == "run":
                api.data[path]["run_attempt"] = 2
            elif fault == "environment":
                api.data[path]["can_admins_bypass"] = True
            elif fault == "branches":
                api.data[path]["branch_policies"][0]["name"] = "*"
            else:
                api.data[path]["custom_deployment_protection_rules"] = [{"enabled": True}]
                api.data[path]["total_count"] = 1
    github.fault = drift
    assert github.inspect()["policy_evidence_valid"] is False


@pytest.mark.parametrize("field,value", [
    ("can_admins_bypass", True), ("can_admins_bypass", None),
    ("deployment_branch_policy", None), ("protection_rules", []),
])
def test_missing_or_weak_environment_protections_hold(field, value):
    github = GitHub()
    github.data[ENV][field] = value
    assert github.inspect()["policy_evidence_valid"] is False


def test_published_environment_rest_schema_cannot_prove_admin_bypass_disabled():
    github = GitHub()
    del github.data[ENV]["can_admins_bypass"]
    assert github.inspect()["blockers"] == ["ENVIRONMENT_BYPASS_UNPROVEN"]


@pytest.mark.parametrize("failure", [
    "self_review_enabled", "self_review", "trigger_self_review", "stale_environment",
    "missing", "rejected", "team_only", "unauthorized_user", "rerun", "ambiguous",
])
def test_human_authorization_negative_real_review_history_schema(failure):
    github = GitHub()
    rule = github.data[ENV]["protection_rules"][-1]
    approval = github.data[RUN + "/approvals"][0]
    if failure == "self_review_enabled":
        rule["prevent_self_review"] = False
    elif failure in {"self_review", "trigger_self_review"}:
        github.data[RUN]["actor" if failure == "self_review" else "triggering_actor"]["id"] = 200
    elif failure == "stale_environment":
        approval["environments"][0]["id"] = 11
    elif failure == "missing":
        github.data[RUN + "/approvals"] = []
    elif failure == "rejected":
        approval["state"] = "rejected"
    elif failure == "team_only":
        rule["reviewers"][0]["type"] = "Team"
    elif failure == "unauthorized_user":
        approval["user"] = {"id": 300, "login": "foreign", "type": "User"}
    elif failure == "rerun":
        github.data[RUN]["run_attempt"] = 2
    elif failure == "ambiguous":
        github.data[RUN + "/approvals"].append(deepcopy(approval))
    assert github.inspect()["policy_evidence_valid"] is False


@pytest.mark.parametrize("failure", [
    "foreign_app", "disabled", "both_routes", "pr_wide_check", "foreign_check",
    "stale_sha", "stale_attempt", "stale_time", "foreign_run_url", "failed",
])
def test_existing_release_app_on_demand_authorization_is_bound_to_run_and_attempt(failure):
    github = GitHub(route="app")
    app = github.data[ENV + "/deployment_protection_rules"]["custom_deployment_protection_rules"][0]
    release = github.data[MAIN_CHECKS]["check_runs"][0]
    if failure == "foreign_app":
        app["app"]["id"] = journal.AUTHENTIC_QA_APP_ID
    elif failure == "disabled":
        app["enabled"] = False
    elif failure == "both_routes":
        github.data[ENV]["protection_rules"].append(
            deepcopy(GitHub().data[ENV]["protection_rules"][-1]))
    elif failure == "pr_wide_check":
        release["name"] = journal.RELEASE_CHECK_NAME
    elif failure == "foreign_check":
        release["app"]["id"] = journal.AUTHENTIC_QA_APP_ID
    elif failure == "stale_sha":
        release["head_sha"] = OTHER
    elif failure == "stale_attempt":
        release["external_id"] = f"wow-release:42:2:{MAIN}"
    elif failure == "stale_time":
        release["completed_at"] = "2026-10-20T12:00:00Z"
    elif failure == "foreign_run_url":
        release["details_url"] += "/attempts/999"
    elif failure == "failed":
        release["conclusion"] = "failure"
    assert github.inspect()["policy_evidence_valid"] is False


@pytest.mark.parametrize("field,value", [
    ("event", "push"), ("event", "workflow_run"), ("head_sha", OTHER),
    ("head_branch", "foreign"), ("path", ".github/workflows/foreign.yml"),
    ("repository", {"full_name": "foreign/repo"}), ("id", 43),
])
def test_manual_release_identity_is_not_taken_from_inputs(field, value):
    github = GitHub()
    github.data[RUN][field] = value
    assert github.inspect()["blockers"] == ["MANUAL_PROTECTED_RUN_IDENTITY_INVALID"]


def test_ambiguous_merged_pr_and_unreadable_or_truncated_api_evidence():
    github = GitHub()
    github.data[ROOT + f"/commits/{MAIN}/pulls?per_page=100&page=1"] *= 2
    assert github.inspect()["blockers"] == ["MERGED_PR_PROVENANCE_NOT_UNIQUE"]
    github = GitHub()
    del github.data["user"]
    assert github.inspect()["blockers"] == ["GITHUB_EVIDENCE_UNREADABLE:user"]
    github = GitHub()
    github.data[HEAD_CHECKS]["total_count"] = 500
    assert github.inspect()["blockers"][0].startswith("GITHUB_EVIDENCE_TRUNCATED:")


def test_transport_exceptions_are_redacted_and_no_write_api_is_available():
    github = GitHub()
    def unavailable(api, path):
        raise RuntimeError("untrusted sensitive transport body")
    github.fault = unavailable
    assert github.inspect()["blockers"] == ["GITHUB_EVIDENCE_UNREADABLE_OR_SCHEMA_INVALID"]


def test_candidate_neither_changes_legacy_role_admission_nor_adds_a_writer():
    github = GitHub()
    assert github.inspect()["policy_evidence_valid"] is True
    assert journal.role_admission(
        github.checks, qa_app_id=journal.AUTHENTIC_QA_APP_ID,
        release_app_id=journal.AUTHENTIC_RELEASE_APP_ID, source_head=HEAD,
    ) == "RENDER_RELEASE_ROLE_CHECK_MISSING_OR_FOREIGN:" + journal.RELEASE_CHECK_NAME
    assert journal.RENDER_CREDENTIAL_SCOPE_ATTESTED is False
    repo = Path(__file__).resolve().parents[3]
    workflows = journal.load_workflow_texts(repo / ".github/workflows")
    assert journal.inventory_render_writers(workflows)["foreign_posts"] == []
    assert all("simple_release_policy_candidate" not in source for source in workflows.values())
