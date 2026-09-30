from __future__ import annotations

import pytest

import github_actions_oidc as oidc


def _claims(workflow_ref: str, event_name: str = "schedule") -> dict[str, str]:
    return {
        "repository": oidc.REPOSITORY,
        "repository_id": oidc.REPOSITORY_ID,
        "repository_owner_id": oidc.REPOSITORY_OWNER_ID,
        "ref": oidc.REF,
        "workflow_ref": workflow_ref,
        "runner_environment": "github-hosted",
        "event_name": event_name,
    }


@pytest.mark.parametrize(
    ("workflow_ref", "suffix"),
    [
        (oidc.NFL_PROP_FORWARD_EVIDENCE_WORKFLOW_REF, "/.github/workflows/wow-v17-nfl-prop-forward-evidence.yml@refs/heads/main"),
        (oidc.MLB_PROP_FORWARD_EVIDENCE_WORKFLOW_REF, "/.github/workflows/wow-v17-mlb-prop-forward-evidence.yml@refs/heads/main"),
        (oidc.WNBA_PROP_FORWARD_EVIDENCE_WORKFLOW_REF, "/.github/workflows/wow-v17-wnba-prop-forward-evidence.yml@refs/heads/main"),
        (oidc.PRIORITY_PROP_LIFECYCLE_WORKFLOW_REF, "/.github/workflows/wow-v17-priority-prop-lifecycle.yml@refs/heads/main"),
    ],
)
def test_prop_evidence_workflow_is_exact_main_pinned_and_authorized(workflow_ref: str, suffix: str):
    assert workflow_ref.endswith(suffix)
    assert workflow_ref in oidc.PROP_EVIDENCE_WORKFLOW_REFS
    assert workflow_ref not in oidc.ALLOWED_WORKFLOW_REFS
    assert oidc.validate_github_actions_claims(_claims(workflow_ref))["workflow_ref"] == workflow_ref


def test_prop_evidence_trust_set_contains_only_the_four_expected_workflows():
    assert oidc.PROP_EVIDENCE_WORKFLOW_REFS == frozenset({
        oidc.NFL_PROP_FORWARD_EVIDENCE_WORKFLOW_REF,
        oidc.MLB_PROP_FORWARD_EVIDENCE_WORKFLOW_REF,
        oidc.WNBA_PROP_FORWARD_EVIDENCE_WORKFLOW_REF,
        oidc.PRIORITY_PROP_LIFECYCLE_WORKFLOW_REF,
    })


def test_priority_prop_lifecycle_workflow_run_is_authorized_after_exact_deploy():
    claims = _claims(oidc.PRIORITY_PROP_LIFECYCLE_WORKFLOW_REF, "workflow_run")
    assert oidc.validate_github_actions_claims(claims)["event_name"] == "workflow_run"


def test_priority_prop_lifecycle_workflow_call_is_authorized_only_for_exact_reusable_workflow():
    claims = _claims(oidc.PRIORITY_PROP_LIFECYCLE_WORKFLOW_REF, "workflow_call")
    assert oidc.validate_github_actions_claims(claims)["event_name"] == "workflow_call"
    for workflow_ref in (
        oidc.NFL_PROP_FORWARD_EVIDENCE_WORKFLOW_REF,
        oidc.MLB_PROP_FORWARD_EVIDENCE_WORKFLOW_REF,
        oidc.WNBA_PROP_FORWARD_EVIDENCE_WORKFLOW_REF,
    ):
        with pytest.raises(oidc.GitHubOIDCValidationError, match="EVENT_NOT_ALLOWED"):
            oidc.validate_github_actions_claims(_claims(workflow_ref, "workflow_call"))


@pytest.mark.parametrize("event_name", ["push", "schedule", "workflow_dispatch"])
def test_priority_prop_lifecycle_existing_non_pr_events_remain_authorized(event_name: str):
    claims = _claims(oidc.PRIORITY_PROP_LIFECYCLE_WORKFLOW_REF, event_name)
    assert oidc.validate_github_actions_claims(claims)["event_name"] == event_name


@pytest.mark.parametrize(
    "workflow_ref",
    [
        oidc.NFL_PROP_FORWARD_EVIDENCE_WORKFLOW_REF,
        oidc.MLB_PROP_FORWARD_EVIDENCE_WORKFLOW_REF,
        oidc.WNBA_PROP_FORWARD_EVIDENCE_WORKFLOW_REF,
        oidc.PRIORITY_PROP_LIFECYCLE_WORKFLOW_REF,
    ],
)
def test_prop_evidence_workflows_never_accept_pull_request_tokens(workflow_ref: str):
    with pytest.raises(oidc.GitHubOIDCValidationError, match="EVENT_NOT_ALLOWED"):
        oidc.validate_github_actions_claims(_claims(workflow_ref, "pull_request"))


def test_other_prop_evidence_workflows_do_not_gain_workflow_run_authority():
    for workflow_ref in (
        oidc.NFL_PROP_FORWARD_EVIDENCE_WORKFLOW_REF,
        oidc.MLB_PROP_FORWARD_EVIDENCE_WORKFLOW_REF,
        oidc.WNBA_PROP_FORWARD_EVIDENCE_WORKFLOW_REF,
    ):
        with pytest.raises(oidc.GitHubOIDCValidationError, match="EVENT_NOT_ALLOWED"):
            oidc.validate_github_actions_claims(_claims(workflow_ref, "workflow_run"))


def test_unlisted_prop_workflow_still_fails_closed():
    unknown = f"{oidc.REPOSITORY}/.github/workflows/wow-v17-unlisted-prop-evidence.yml@{oidc.REF}"
    with pytest.raises(oidc.GitHubOIDCValidationError, match="WORKFLOW_REF_MISMATCH"):
        oidc.validate_github_actions_claims(_claims(unknown))
