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
        "workflow_ref": oidc.SPREAD_FORWARD_PRODUCTION_CANARY_WORKFLOW_REF,
        "event_name": "workflow_run",
    }
    claims.update(overrides)
    return claims


def test_spread_forward_production_canary_workflow_run_is_explicitly_authorized():
    claims = _claims()
    assert oidc.SPREAD_FORWARD_PRODUCTION_CANARY_WORKFLOW_REF in oidc.LIVE_CANARY_WORKFLOW_REFS
    assert oidc.SPREAD_FORWARD_PRODUCTION_CANARY_WORKFLOW_REF not in oidc.ALLOWED_WORKFLOW_REFS
    assert oidc.validate_github_actions_claims(claims) == claims


def test_spread_forward_production_canary_workflow_call_is_explicitly_authorized():
    claims = _claims(event_name="workflow_call")
    assert oidc.validate_github_actions_claims(claims) == claims


@pytest.mark.parametrize("event_name", ["push", "schedule", "workflow_dispatch", "pull_request"])
def test_spread_forward_production_canary_rejects_other_events(event_name):
    with pytest.raises(oidc.GitHubOIDCValidationError, match="GITHUB_OIDC_EVENT_NOT_ALLOWED"):
        oidc.validate_github_actions_claims(_claims(event_name=event_name))


def test_workflow_run_is_not_broadened_to_existing_internal_workflows():
    with pytest.raises(oidc.GitHubOIDCValidationError, match="GITHUB_OIDC_EVENT_NOT_ALLOWED"):
        oidc.validate_github_actions_claims(
            _claims(workflow_ref=oidc.SPREAD_MARGIN_REPLAY_WORKFLOW_REF, event_name="workflow_run")
        )


def test_spread_forward_production_canary_non_main_ref_remains_rejected():
    with pytest.raises(oidc.GitHubOIDCValidationError, match="GITHUB_OIDC_REF_MISMATCH"):
        oidc.validate_github_actions_claims(
            _claims(
                ref="refs/heads/feature",
                workflow_ref=(
                    f"{oidc.REPOSITORY}/.github/workflows/"
                    "wow-v17-spread-forward-production-canary.yml@refs/heads/feature"
                ),
            )
        )


def test_lookalike_spread_canary_workflow_remains_rejected():
    with pytest.raises(oidc.GitHubOIDCValidationError, match="GITHUB_OIDC_WORKFLOW_REF_MISMATCH"):
        oidc.validate_github_actions_claims(
            _claims(
                workflow_ref=(
                    f"{oidc.REPOSITORY}/.github/workflows/"
                    "wow-v17-spread-forward-production-canary-copy.yml@refs/heads/main"
                )
            )
        )


def test_existing_nfl_live_canary_still_accepts_only_existing_event_contract():
    claims = _claims(
        workflow_ref=oidc.NFL_PROP_LIVE_CANARY_WORKFLOW_REF,
        event_name="push",
    )
    assert oidc.validate_github_actions_claims(claims) == claims
    with pytest.raises(oidc.GitHubOIDCValidationError, match="GITHUB_OIDC_EVENT_NOT_ALLOWED"):
        oidc.validate_github_actions_claims({**claims, "event_name": "workflow_run"})
