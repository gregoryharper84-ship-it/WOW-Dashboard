from __future__ import annotations

import pytest

import github_actions_oidc as oidc


def _claims(*, job_workflow_ref: str, event_name: str = "workflow_run", **overrides):
    claims = {
        "repository": oidc.REPOSITORY,
        "repository_id": oidc.REPOSITORY_ID,
        "repository_owner_id": oidc.REPOSITORY_OWNER_ID,
        "ref": oidc.REF,
        "runner_environment": "github-hosted",
        "workflow_ref": oidc.POST_DEPLOY_ORCHESTRATOR_WORKFLOW_REF,
        "job_workflow_ref": job_workflow_ref,
        "event_name": event_name,
    }
    claims.update(overrides)
    return claims


@pytest.mark.parametrize(
    "callee",
    [
        oidc.SPREAD_CERTIFICATION_REPLAY_WORKFLOW_REF,
        oidc.PRIORITY_PROP_LIFECYCLE_WORKFLOW_REF,
        oidc.SPREAD_FORWARD_PRODUCTION_CANARY_WORKFLOW_REF,
    ],
)
def test_exact_post_deploy_reusable_pairs_are_authorized(callee: str):
    claims = _claims(job_workflow_ref=callee)
    assert callee in oidc.POST_DEPLOY_REUSABLE_WORKFLOW_REFS
    assert oidc.validate_github_actions_claims(claims) == claims


def test_orchestrator_without_reusable_callee_is_rejected():
    with pytest.raises(
        oidc.GitHubOIDCValidationError,
        match="GITHUB_OIDC_JOB_WORKFLOW_REF_MISMATCH",
    ):
        oidc.validate_github_actions_claims(
            _claims(job_workflow_ref="")
        )


def test_orchestrator_lookalike_reusable_callee_is_rejected():
    lookalike = (
        f"{oidc.REPOSITORY}/.github/workflows/"
        "wow-v17-priority-prop-lifecycle-copy.yml@refs/heads/main"
    )
    with pytest.raises(
        oidc.GitHubOIDCValidationError,
        match="GITHUB_OIDC_JOB_WORKFLOW_REF_MISMATCH",
    ):
        oidc.validate_github_actions_claims(
            _claims(job_workflow_ref=lookalike)
        )


def test_orchestrator_non_main_reusable_callee_is_rejected():
    non_main = oidc.PRIORITY_PROP_LIFECYCLE_WORKFLOW_REF.replace(
        "@refs/heads/main", "@refs/heads/feature"
    )
    with pytest.raises(
        oidc.GitHubOIDCValidationError,
        match="GITHUB_OIDC_JOB_WORKFLOW_REF_MISMATCH",
    ):
        oidc.validate_github_actions_claims(
            _claims(job_workflow_ref=non_main)
        )


@pytest.mark.parametrize("event_name", ["push", "schedule", "workflow_dispatch", "pull_request"])
def test_post_deploy_reusable_pairs_accept_only_workflow_run(event_name: str):
    with pytest.raises(
        oidc.GitHubOIDCValidationError,
        match="GITHUB_OIDC_EVENT_NOT_ALLOWED",
    ):
        oidc.validate_github_actions_claims(
            _claims(
                job_workflow_ref=oidc.PRIORITY_PROP_LIFECYCLE_WORKFLOW_REF,
                event_name=event_name,
            )
        )


def test_unlisted_caller_cannot_borrow_approved_reusable_callee():
    unlisted_caller = (
        f"{oidc.REPOSITORY}/.github/workflows/"
        "wow-v17-post-deploy-verification-orchestrator-copy.yml@refs/heads/main"
    )
    with pytest.raises(
        oidc.GitHubOIDCValidationError,
        match="GITHUB_OIDC_WORKFLOW_REF_MISMATCH",
    ):
        oidc.validate_github_actions_claims(
            _claims(
                job_workflow_ref=oidc.PRIORITY_PROP_LIFECYCLE_WORKFLOW_REF,
                workflow_ref=unlisted_caller,
            )
        )
