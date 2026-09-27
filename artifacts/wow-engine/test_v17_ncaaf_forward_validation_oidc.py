from __future__ import annotations

import pytest

import github_actions_oidc as oidc


def _claims(event_name: str = "push") -> dict[str, str]:
    return {
        "repository": oidc.REPOSITORY,
        "repository_id": oidc.REPOSITORY_ID,
        "repository_owner_id": oidc.REPOSITORY_OWNER_ID,
        "ref": oidc.REF,
        "workflow_ref": oidc.NCAAF_FORWARD_VALIDATION_WORKFLOW_REF,
        "runner_environment": "github-hosted",
        "event_name": event_name,
    }


def test_ncaaf_forward_validation_is_exact_protected_main_canary():
    assert oidc.NCAAF_FORWARD_VALIDATION_WORKFLOW_REF.endswith(
        "/.github/workflows/wow-v17-ncaaf-forward-validation.yml@refs/heads/main"
    )
    assert oidc.NCAAF_FORWARD_VALIDATION_WORKFLOW_REF in oidc.LIVE_CANARY_WORKFLOW_REFS
    assert oidc.NCAAF_FORWARD_VALIDATION_WORKFLOW_REF not in oidc.ALLOWED_WORKFLOW_REFS
    assert oidc.validate_github_actions_claims(_claims())["event_name"] == "push"


def test_ncaaf_forward_validation_pr_token_is_rejected():
    with pytest.raises(oidc.GitHubOIDCValidationError, match="EVENT_NOT_ALLOWED"):
        oidc.validate_github_actions_claims(_claims("pull_request"))


def test_ncaaf_forward_validation_still_rejects_non_main_ref():
    claims = _claims()
    claims["ref"] = "refs/heads/feature"
    with pytest.raises(oidc.GitHubOIDCValidationError, match="REF_MISMATCH"):
        oidc.validate_github_actions_claims(claims)
