"""Strict GitHub Actions OIDC verifier for the WOW V17 Multi-Scout workflow.

This is an internal automation credential path only. It does not replace
WOW_ACTION_API_KEY for Custom GPT Actions and it never authorizes wager
execution. Tokens are accepted only from GitHub's OIDC issuer for this exact
repository, workflow, main ref, and a small set of non-PR production events.
"""
from __future__ import annotations

import inspect
import os
import secrets
from typing import Any

import jwt
from fastapi import Depends, Header, HTTPException
from jwt import PyJWKClient

ISSUER = "https://token.actions.githubusercontent.com"
JWKS_URL = f"{ISSUER}/.well-known/jwks"
AUDIENCE = "wow-v17-multiscout"
REPOSITORY = "gregoryharper84-ship-it/WOW-Dashboard"
REPOSITORY_ID = "1240256887"
REPOSITORY_OWNER_ID = "285088163"
REF = "refs/heads/main"
WORKFLOW_REF = f"{REPOSITORY}/.github/workflows/wow-v17-nightly-multiscout.yml@{REF}"
ALLOWED_EVENTS = frozenset({"push", "schedule", "workflow_dispatch"})


class GitHubOIDCValidationError(ValueError):
    """Raised when an OIDC token cannot satisfy the exact Scout trust policy."""


def validate_github_actions_claims(claims: dict[str, Any]) -> dict[str, Any]:
    checks = {
        "repository": REPOSITORY,
        "repository_id": REPOSITORY_ID,
        "repository_owner_id": REPOSITORY_OWNER_ID,
        "ref": REF,
        "workflow_ref": WORKFLOW_REF,
        "runner_environment": "github-hosted",
    }
    for field, expected in checks.items():
        actual = str(claims.get(field) or "")
        if actual != expected:
            raise GitHubOIDCValidationError(f"GITHUB_OIDC_{field.upper()}_MISMATCH")
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
    """Authorize an existing WOW Action bearer or the exact Scout OIDC token."""
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

    Production supplies Depends(_require_action_api_key). Lower-layer tests may
    supply a permissive dependency. We try that exact dependency first; only a
    rejected production-style auth attempt falls through to the strict OIDC
    verifier. This keeps existing test/staging injection behavior intact.
    """
    existing_fn = getattr(existing_auth_dependency, "dependency", None)
    if existing_fn is None and callable(existing_auth_dependency):
        existing_fn = existing_auth_dependency

    def _combined(authorization: str | None = Header(default=None)) -> None:
        prior_error: HTTPException | None = None
        if callable(existing_fn):
            try:
                params = inspect.signature(existing_fn).parameters
                if "authorization" in params:
                    existing_fn(authorization)
                else:
                    existing_fn()
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
    "AUDIENCE",
    "GitHubOIDCValidationError",
    "authorize_action_key_or_multiscout_oidc",
    "scout_route_auth_dependency",
    "validate_github_actions_claims",
    "verify_github_actions_oidc",
]
