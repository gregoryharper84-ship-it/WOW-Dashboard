import pytest

import github_actions_oidc as oidc


def _claims(event_name="workflow_run"):
    return {
        "repository": oidc.REPOSITORY,
        "repository_id": oidc.REPOSITORY_ID,
        "repository_owner_id": oidc.REPOSITORY_OWNER_ID,
        "ref": oidc.REF,
        "workflow_ref": oidc.WORKFLOW_REF,
        "runner_environment": "github-hosted",
        "event_name": event_name,
    }


def test_exact_protected_multiscout_workflow_run_is_accepted():
    assert oidc.validate_github_actions_claims(_claims())["event_name"] == "workflow_run"


def test_pull_request_remains_fail_closed():
    with pytest.raises(oidc.GitHubOIDCValidationError, match="EVENT_NOT_ALLOWED"):
        oidc.validate_github_actions_claims(_claims("pull_request"))


def test_workflow_identity_remains_exactly_pinned():
    claims = _claims()
    claims["workflow_ref"] = (
        "gregoryharper84-ship-it/WOW-Dashboard/"
        ".github/workflows/untrusted.yml@refs/heads/main"
    )
    with pytest.raises(oidc.GitHubOIDCValidationError, match="WORKFLOW_REF_MISMATCH"):
        oidc.validate_github_actions_claims(claims)
