"""Strict GitHub Actions OIDC verifier for approved WOW V17 internal workflows.

This is an internal automation credential path only. It does not replace
WOW_ACTION_API_KEY for Custom GPT Actions and it never authorizes wager
execution. Tokens are accepted only from GitHub's OIDC issuer for this exact
repository, explicit protected-main workflow identities, and a small set of
non-PR production events.
"""
from __future__ import annotations

import inspect
import os
import secrets
from typing import Any

import jwt
from fastapi import Depends, Header, HTTPException, Request
from jwt import PyJWKClient

ISSUER = "https://token.actions.githubusercontent.com"
JWKS_URL = f"{ISSUER}/.well-known/jwks"
AUDIENCE = "wow-v17-multiscout"
REPOSITORY = "gregoryharper84-ship-it/WOW-Dashboard"
REPOSITORY_ID = "1240256887"
REPOSITORY_OWNER_ID = "285088163"
REF = "refs/heads/main"
WORKFLOW_REF = f"{REPOSITORY}/.github/workflows/wow-v17-nightly-multiscout.yml@{REF}"
DAILY_SNAPSHOT_WORKFLOW_REF = (
    f"{REPOSITORY}/.github/workflows/wow-v17-daily-snapshot.yml@{REF}"
)
TEAM_EVENT_RECOVERABLE_REFRESH_WORKFLOW_REF = (
    f"{REPOSITORY}/.github/workflows/wow-v17-team-event-recoverable-refresh.yml@{REF}"
)
NFL_FORWARD_SHADOW_WORKFLOW_REF = (
    f"{REPOSITORY}/.github/workflows/wow-v17-nfl-forward-shadow.yml@{REF}"
)
LLP_SHADOW_OBSERVER_WORKFLOW_REF = (
    f"{REPOSITORY}/.github/workflows/wow-v17-llp-shadow-observer.yml@{REF}"
)
NFL_PROP_LIVE_CANARY_WORKFLOW_REF = (
    f"{REPOSITORY}/.github/workflows/wow-v17-nfl-prop-live-canary.yml@{REF}"
)
BASKETBALL_MODEL_MAINTENANCE_WORKFLOW_REF = (
    f"{REPOSITORY}/.github/workflows/wow-v17-basketball-model-maintenance.yml@{REF}"
)
NCAAF_MODEL_MAINTENANCE_WORKFLOW_REF = (
    f"{REPOSITORY}/.github/workflows/wow-v17-ncaaf-model-maintenance.yml@{REF}"
)
WNBA_PROP_CANDIDATE_WORKFLOW_REF = (
    f"{REPOSITORY}/.github/workflows/wow-v17-wnba-prop-candidate.yml@{REF}"
)
NHL_MODEL_MAINTENANCE_WORKFLOW_REF = (
    f"{REPOSITORY}/.github/workflows/wow-v17-nhl-model-maintenance.yml@{REF}"
)
PROP_ACTION_WORKFLOW_REF = (
    f"{REPOSITORY}/.github/workflows/wow-v17-canonical-prop-action.yml@{REF}"
)
PROP_LIFECYCLE_AUTOPILOT_WORKFLOW_REF = (
    f"{REPOSITORY}/.github/workflows/wow-v17-prop-lifecycle-autopilot.yml@{REF}"
)
FIRST_SIX_MODEL_MAINTENANCE_WORKFLOW_REF = (
    f"{REPOSITORY}/.github/workflows/wow-v17-first-six-model-maintenance.yml@{REF}"
)
FIRST_SIX_TRANSPORT_RESCUE_WORKFLOW_REF = (
    f"{REPOSITORY}/.github/workflows/wow-v17-first-six-transport-rescue.yml@{REF}"
)
MLB_1IP_LINE_EXPANSION_MAINTENANCE_WORKFLOW_REF = (
    f"{REPOSITORY}/.github/workflows/wow-v17-mlb-1ip-line-expansion-maintenance.yml@{REF}"
)
SPREAD_MARGIN_REPLAY_WORKFLOW_REF = (
    f"{REPOSITORY}/.github/workflows/wow-v17-spread-margin-replay.yml@{REF}"
)
ALLOWED_WORKFLOW_REFS = frozenset({
    WORKFLOW_REF,
    DAILY_SNAPSHOT_WORKFLOW_REF,
    TEAM_EVENT_RECOVERABLE_REFRESH_WORKFLOW_REF,
    NFL_FORWARD_SHADOW_WORKFLOW_REF,
    LLP_SHADOW_OBSERVER_WORKFLOW_REF,
    BASKETBALL_MODEL_MAINTENANCE_WORKFLOW_REF,
    NCAAF_MODEL_MAINTENANCE_WORKFLOW_REF,
    WNBA_PROP_CANDIDATE_WORKFLOW_REF,
    NHL_MODEL_MAINTENANCE_WORKFLOW_REF,
    PROP_ACTION_WORKFLOW_REF,
    PROP_LIFECYCLE_AUTOPILOT_WORKFLOW_REF,
    FIRST_SIX_MODEL_MAINTENANCE_WORKFLOW_REF,
    FIRST_SIX_TRANSPORT_RESCUE_WORKFLOW_REF,
    MLB_1IP_LINE_EXPANSION_MAINTENANCE_WORKFLOW_REF,
    SPREAD_MARGIN_REPLAY_WORKFLOW_REF,
})
# Live scoring canaries are kept separate from the long-lived automation set so
# their trust boundary stays explicit and independently reviewable.
LIVE_CANARY_WORKFLOW_REFS = frozenset({NFL_PROP_LIVE_CANARY_WORKFLOW_REF})
ALLOWED_EVENTS = frozenset({"push", "schedule", "workflow_dispatch"})


class GitHubOIDCValidationError(ValueError):
    """Raised when an OIDC token cannot satisfy the exact internal trust policy."""


def validate_github_actions_claims(claims: dict[str, Any]) -> dict[str, Any]:
    checks = {
        "repository": REPOSITORY,
        "repository_id": REPOSITORY_ID,
        "repository_owner_id": REPOSITORY_OWNER_ID,
        "ref": REF,
        "runner_environment": "github-hosted",
    }
    for field, expected in checks.items():
        actual = str(claims.get(field) or "")
        if actual != expected:
            raise GitHubOIDCValidationError(f"GITHUB_OIDC_{field.upper()}_MISMATCH")
    workflow_ref = str(claims.get("workflow_ref") or "")
    if workflow_ref not in ALLOWED_WORKFLOW_REFS and workflow_ref not in LIVE_CANARY_WORKFLOW_REFS:
        raise GitHubOIDCValidationError("GITHUB_OIDC_WORKFLOW_REF_MISMATCH")
    event_name = str(claims.get("event_name") or "")
    if event_name not in ALLOWED_EVENTS:
        raise GitHubOIDCValidationError("GITHUB_OIDC_EVENT_NOT_ALLOWED")
    return dict(claims)


def verify_github_actions_oidc(token: str, *, jwk_client: PyJWKClient | None = None) -> dict[str, Any]:
    if not isinstance(token, str) or not token.strip():
        raise GitHubOIDCValidationError("GITHUB_OIDC_TOKEN_MISSING")
    try:
        client = jwk_client or PyJWKClient(JWKS_URL, cache_keys=True)
        signing_key = client.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=AUDIENCE,
            issuer=ISSUER,
            options={"require": ["exp", "iat", "nbf", "jti"]},
        )
    except Exception as exc:
        raise GitHubOIDCValidationError("GITHUB_OIDC_SIGNATURE_OR_STANDARD_CLAIMS_INVALID") from exc
    return validate_github_actions_claims(claims)


def authorize_action_key_or_multiscout_oidc(authorization: str | None) -> str:
    """Authorize an existing WOW Action bearer or an approved workflow OIDC token."""
    if not authorization or not authorization.startswith("Bearer "):
        raise GitHubOIDCValidationError("SCOUT_ROUTE_AUTH_REQUIRED")
    supplied = authorization[len("Bearer ") :]
    configured = os.environ.get("WOW_ACTION_API_KEY")
    if configured and secrets.compare_digest(supplied, configured):
        return "WOW_ACTION_API_KEY"
    verify_github_actions_oidc(supplied)
    return "GITHUB_ACTIONS_OIDC"


def scout_route_auth_dependency(existing_auth_dependency: Any) -> Any:
    """Return a FastAPI dependency preserving the caller's existing auth seam.

    ``authorization`` remains the first positional argument because several
    tests and compatibility callers invoke the returned dependency directly.
    FastAPI also injects the current request and the MCP acceptance marker so a
    request-aware wrapped dependency can receive them without the OIDC layer
    discarding security context.
    """
    existing_fn = getattr(existing_auth_dependency, "dependency", None)
    if existing_fn is None and callable(existing_auth_dependency):
        existing_fn = existing_auth_dependency

    def _combined(
        authorization: str | None = Header(default=None),
        request: Request = None,
        x_wow_mcp_acceptance: str | None = Header(default=None, alias="X-WOW-MCP-Acceptance"),
    ) -> None:
        prior_error: HTTPException | None = None
        if callable(existing_fn):
            try:
                params = inspect.signature(existing_fn).parameters
                call_kwargs: dict[str, Any] = {}
                if "authorization" in params:
                    call_kwargs["authorization"] = authorization
                if "request" in params:
                    call_kwargs["request"] = request
                if "x_wow_mcp_acceptance" in params:
                    call_kwargs["x_wow_mcp_acceptance"] = x_wow_mcp_acceptance
                existing_fn(**call_kwargs)
                return
            except HTTPException as exc:
                prior_error = exc
        try:
            authorize_action_key_or_multiscout_oidc(authorization)
            return
        except GitHubOIDCValidationError as exc:
            if prior_error is not None:
                raise prior_error
            raise HTTPException(
                status_code=401,
                detail={"code": "SCOUT_ROUTE_AUTH_INVALID", "can_execute": False},
            ) from exc

    return Depends(_combined)


__all__ = [
    "ALLOWED_WORKFLOW_REFS",
    "LIVE_CANARY_WORKFLOW_REFS",
    "AUDIENCE",
    "BASKETBALL_MODEL_MAINTENANCE_WORKFLOW_REF",
    "DAILY_SNAPSHOT_WORKFLOW_REF",
    "TEAM_EVENT_RECOVERABLE_REFRESH_WORKFLOW_REF",
    "FIRST_SIX_MODEL_MAINTENANCE_WORKFLOW_REF",
    "FIRST_SIX_TRANSPORT_RESCUE_WORKFLOW_REF",
    "LLP_SHADOW_OBSERVER_WORKFLOW_REF",
    "MLB_1IP_LINE_EXPANSION_MAINTENANCE_WORKFLOW_REF",
    "SPREAD_MARGIN_REPLAY_WORKFLOW_REF",
    "GitHubOIDCValidationError",
    "NCAAF_MODEL_MAINTENANCE_WORKFLOW_REF",
    "NFL_FORWARD_SHADOW_WORKFLOW_REF",
    "NFL_PROP_LIVE_CANARY_WORKFLOW_REF",
    "NHL_MODEL_MAINTENANCE_WORKFLOW_REF",
    "PROP_ACTION_WORKFLOW_REF",
    "PROP_LIFECYCLE_AUTOPILOT_WORKFLOW_REF",
    "WNBA_PROP_CANDIDATE_WORKFLOW_REF",
    "WORKFLOW_REF",
    "authorize_action_key_or_multiscout_oidc",
    "scout_route_auth_dependency",
    "validate_github_actions_claims",
    "verify_github_actions_oidc",
]