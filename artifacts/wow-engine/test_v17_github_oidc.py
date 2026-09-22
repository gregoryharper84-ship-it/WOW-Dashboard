from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import Depends, HTTPException

import github_actions_oidc as oidc


def _claims():
    return {
        "repository": oidc.REPOSITORY,
        "repository_id": oidc.REPOSITORY_ID,
        "repository_owner_id": oidc.REPOSITORY_OWNER_ID,
        "ref": oidc.REF,
        "workflow_ref": oidc.WORKFLOW_REF,
        "runner_environment": "github-hosted",
        "event_name": "push",
    }


def test_exact_protected_main_multiscout_claims_are_accepted():
    assert oidc.validate_github_actions_claims(_claims())["repository_id"] == oidc.REPOSITORY_ID


def test_protected_workflow_ref_stays_pinned_to_nightly_multiscout_main():
    assert oidc.WORKFLOW_REF.endswith(
        "/.github/workflows/wow-v17-nightly-multiscout.yml@refs/heads/main"
    )


@pytest.mark.parametrize(
    "workflow_ref,expected_suffix",
    [
        (oidc.DAILY_SNAPSHOT_WORKFLOW_REF, "/.github/workflows/wow-v17-daily-snapshot.yml@refs/heads/main"),
        (oidc.NFL_FORWARD_SHADOW_WORKFLOW_REF, "/.github/workflows/wow-v17-nfl-forward-shadow.yml@refs/heads/main"),
        (oidc.LLP_SHADOW_OBSERVER_WORKFLOW_REF, "/.github/workflows/wow-v17-llp-shadow-observer.yml@refs/heads/main"),
        (oidc.BASKETBALL_MODEL_MAINTENANCE_WORKFLOW_REF, "/.github/workflows/wow-v17-basketball-model-maintenance.yml@refs/heads/main"),
        (oidc.NCAAF_MODEL_MAINTENANCE_WORKFLOW_REF, "/.github/workflows/wow-v17-ncaaf-model-maintenance.yml@refs/heads/main"),
        (oidc.WNBA_PROP_CANDIDATE_WORKFLOW_REF, "/.github/workflows/wow-v17-wnba-prop-candidate.yml@refs/heads/main"),
        (oidc.NHL_MODEL_MAINTENANCE_WORKFLOW_REF, "/.github/workflows/wow-v17-nhl-model-maintenance.yml@refs/heads/main"),
        (oidc.PROP_ACTION_WORKFLOW_REF, "/.github/workflows/wow-v17-canonical-prop-action.yml@refs/heads/main"),
        (oidc.PROP_LIFECYCLE_AUTOPILOT_WORKFLOW_REF, "/.github/workflows/wow-v17-prop-lifecycle-autopilot.yml@refs/heads/main"),
        (oidc.FIRST_SIX_MODEL_MAINTENANCE_WORKFLOW_REF, "/.github/workflows/wow-v17-first-six-model-maintenance.yml@refs/heads/main"),
        (oidc.FIRST_SIX_TRANSPORT_RESCUE_WORKFLOW_REF, "/.github/workflows/wow-v17-first-six-transport-rescue.yml@refs/heads/main"),
        (oidc.MLB_1IP_LINE_EXPANSION_MAINTENANCE_WORKFLOW_REF, "/.github/workflows/wow-v17-mlb-1ip-line-expansion-maintenance.yml@refs/heads/main"),
    ],
)
def test_internal_workflow_is_explicitly_pinned_and_accepted(workflow_ref, expected_suffix):
    assert workflow_ref.endswith(expected_suffix)
    claims = _claims()
    claims["workflow_ref"] = workflow_ref
    claims["event_name"] = "schedule"
    assert oidc.validate_github_actions_claims(claims)["workflow_ref"] == workflow_ref


def test_workflow_allowlist_contains_only_known_internal_workflows():
    assert oidc.ALLOWED_WORKFLOW_REFS == frozenset({
        oidc.WORKFLOW_REF,
        oidc.DAILY_SNAPSHOT_WORKFLOW_REF,
        oidc.NFL_FORWARD_SHADOW_WORKFLOW_REF,
        oidc.LLP_SHADOW_OBSERVER_WORKFLOW_REF,
        oidc.BASKETBALL_MODEL_MAINTENANCE_WORKFLOW_REF,
        oidc.NCAAF_MODEL_MAINTENANCE_WORKFLOW_REF,
        oidc.WNBA_PROP_CANDIDATE_WORKFLOW_REF,
        oidc.NHL_MODEL_MAINTENANCE_WORKFLOW_REF,
        oidc.PROP_ACTION_WORKFLOW_REF,
        oidc.PROP_LIFECYCLE_AUTOPILOT_WORKFLOW_REF,
        oidc.FIRST_SIX_MODEL_MAINTENANCE_WORKFLOW_REF,
        oidc.FIRST_SIX_TRANSPORT_RESCUE_WORKFLOW_REF,
        oidc.MLB_1IP_LINE_EXPANSION_MAINTENANCE_WORKFLOW_REF,
    })


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("repository", "someone/else"),
        ("repository_id", "1"),
        ("repository_owner_id", "2"),
        ("ref", "refs/heads/feature"),
        ("workflow_ref", "gregoryharper84-ship-it/WOW-Dashboard/.github/workflows/other.yml@refs/heads/main"),
        ("runner_environment", "self-hosted"),
    ],
)
def test_oidc_claim_identity_mismatch_fails_closed(field, bad):
    claims = _claims()
    claims[field] = bad
    with pytest.raises(oidc.GitHubOIDCValidationError):
        oidc.validate_github_actions_claims(claims)


@pytest.mark.parametrize(
    "workflow_ref",
    [
        oidc.WORKFLOW_REF,
        oidc.DAILY_SNAPSHOT_WORKFLOW_REF,
        oidc.NFL_FORWARD_SHADOW_WORKFLOW_REF,
        oidc.LLP_SHADOW_OBSERVER_WORKFLOW_REF,
        oidc.BASKETBALL_MODEL_MAINTENANCE_WORKFLOW_REF,
        oidc.NCAAF_MODEL_MAINTENANCE_WORKFLOW_REF,
        oidc.WNBA_PROP_CANDIDATE_WORKFLOW_REF,
        oidc.NHL_MODEL_MAINTENANCE_WORKFLOW_REF,
        oidc.PROP_ACTION_WORKFLOW_REF,
        oidc.PROP_LIFECYCLE_AUTOPILOT_WORKFLOW_REF,
        oidc.FIRST_SIX_MODEL_MAINTENANCE_WORKFLOW_REF,
        oidc.FIRST_SIX_TRANSPORT_RESCUE_WORKFLOW_REF,
        oidc.MLB_1IP_LINE_EXPANSION_MAINTENANCE_WORKFLOW_REF,
    ],
)
def test_pull_request_oidc_is_never_authorized_for_internal_workflows(workflow_ref):
    claims = _claims()
    claims["workflow_ref"] = workflow_ref
    claims["event_name"] = "pull_request"
    with pytest.raises(oidc.GitHubOIDCValidationError, match="EVENT_NOT_ALLOWED"):
        oidc.validate_github_actions_claims(claims)


def test_existing_action_key_remains_first_class_auth(monkeypatch):
    monkeypatch.setenv("WOW_ACTION_API_KEY", "action-secret")
    assert oidc.authorize_action_key_or_multiscout_oidc("Bearer action-secret") == "WOW_ACTION_API_KEY"


def test_oidc_is_only_fallback_after_action_key_miss(monkeypatch):
    monkeypatch.setenv("WOW_ACTION_API_KEY", "action-secret")
    seen = []
    monkeypatch.setattr(oidc, "verify_github_actions_oidc", lambda token: seen.append(token) or _claims())
    assert oidc.authorize_action_key_or_multiscout_oidc("Bearer github-token") == "GITHUB_ACTIONS_OIDC"
    assert seen == ["github-token"]


def test_route_dependency_preserves_permissive_test_injection():
    dep = oidc.scout_route_auth_dependency(Depends(lambda: None))
    dep.dependency(None)


def test_route_dependency_falls_back_to_oidc_after_existing_http_reject(monkeypatch):
    def reject(authorization=None):
        raise HTTPException(status_code=401, detail="no")

    monkeypatch.setattr(oidc, "authorize_action_key_or_multiscout_oidc", lambda authorization: "GITHUB_ACTIONS_OIDC")
    dep = oidc.scout_route_auth_dependency(Depends(reject))
    dep.dependency("Bearer github-token")


def test_route_dependency_preserves_original_auth_failure_when_oidc_invalid(monkeypatch):
    def reject(authorization=None):
        raise HTTPException(status_code=401, detail="original-action-auth")

    def fail(_authorization):
        raise oidc.GitHubOIDCValidationError("bad")

    monkeypatch.setattr(oidc, "authorize_action_key_or_multiscout_oidc", fail)
    dep = oidc.scout_route_auth_dependency(Depends(reject))
    with pytest.raises(HTTPException) as caught:
        dep.dependency("Bearer invalid")
    assert caught.value.detail == "original-action-auth"


def test_secret_sync_workflow_is_protected_main_only_and_never_pr_exposed():
    repo_root = Path(__file__).resolve().parents[2]
    workflow = (repo_root / ".github" / "workflows" / "wow-v17-secret-sync.yml").read_text(
        encoding="utf-8"
    )
    assert "pull_request:" not in workflow
    assert "push:" in workflow
    assert "branches:\n      - main" in workflow
    assert '      - ".github/workflows/wow-v17-secret-sync.yml"' in workflow
    assert '      - "artifacts/wow-engine/v17/secret_sync.py"' in workflow
    assert '      - "artifacts/wow-engine/v17/secret_sync_manifest.json"' in workflow
    assert "if: github.ref == 'refs/heads/main'" in workflow
    assert "RENDER_API_KEY: ${{ secrets.RENDER_API_KEY }}" in workflow
    assert "WOW_GITHUB_SECRET_SYNC_TOKEN: ${{ secrets.WOW_GITHUB_SECRET_SYNC_TOKEN }}" in workflow
    assert 'WOW_CAN_EXECUTE: "false"' in workflow
    assert 'WOW_DRY_RUN_ONLY: "true"' in workflow
