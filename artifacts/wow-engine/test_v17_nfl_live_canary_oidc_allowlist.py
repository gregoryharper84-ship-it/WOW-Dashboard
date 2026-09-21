from __future__ import annotations

import pytest

import github_actions_oidc as oidc


def _claims(**overrides):
    claims = {
        "repository": oidc.REPOSITORY,
        "repository_id": oidc.REPOSITORY_ID,
        "repository_owner_id": oidc.REPOSITORY_OWNER_ID,
        "ref": oidc.REF,
        "runner_environment": "github-hosted",
        "workflow_ref": oidc.NFL_PROP_LIVE_CANARY_WORKFLOW_REF,
        "event_name": "push",
    }
    claims.update(overrides)
    return claims


def test_nfl_prop_live_canary_main_workflow_is_explicitly_authorized():
    claims = _claims()
    assert oidc.NFL_PROP_LIVE_CANARY_WORKFLOW_REF in oidc.ALLOWED_WORKFLOW_REFS
    assert oidc.validate_github_actions_claims(claims) == claims


def test_nfl_prop_live_canary_non_main_ref_remains_rejected():
    with pytest.raises(oidc.GitHubOIDCValidationError, match="GITHUB_OIDC_REF_MISMATCH"):
        oidc.validate_github_actions_claims(
            _claims(
                ref="refs/heads/test/v17-nfl-prop-live-canary-20260921",
                workflow_ref=(
                    f"{oidc.REPOSITORY}/.github/workflows/"
                    "wow-v17-nfl-prop-live-canary.yml@refs/heads/"
                    "test/v17-nfl-prop-live-canary-20260921"
                ),
            )
        )


def test_lookalike_canary_workflow_remains_rejected():
    with pytest.raises(oidc.GitHubOIDCValidationError, match="GITHUB_OIDC_WORKFLOW_REF_MISMATCH"):
        oidc.validate_github_actions_claims(
            _claims(
                workflow_ref=(
                    f"{oidc.REPOSITORY}/.github/workflows/"
                    "wow-v17-nfl-prop-live-canary-copy.yml@refs/heads/main"
                )
            )
        )


def test_pull_request_event_remains_rejected_for_live_canary():
    with pytest.raises(oidc.GitHubOIDCValidationError, match="GITHUB_OIDC_EVENT_NOT_ALLOWED"):
        oidc.validate_github_actions_claims(_claims(event_name="pull_request"))
